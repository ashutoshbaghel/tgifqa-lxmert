#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.

import os
import random
from io import BytesIO
import torch
import torch.utils.data
import glob
import slowfast.datasets.decoder as decoder
import slowfast.datasets.transform as transform
import slowfast.datasets.video_container as container
import slowfast.utils.logging as logging
from PIL import Image
import torchvision.transforms.functional as TF
logger = logging.get_logger(__name__)

import numpy as np
class TGIF(torch.utils.data.Dataset):
    """
    Kinetics video loader. Construct the Kinetics video loader, then sample
    clips from the videos. For training and validation, a single clip is
    randomly sampled from every video with random cropping, scaling, and
    flipping. For testing, multiple clips are uniformaly sampled from every
    video with uniform cropping. For uniform cropping, we take the left, center,
    and right crop if the width is larger than height, or take top, center, and
    bottom crop if the height is larger than the width.
    """

    def __init__(self, cfg, mode, root_path= "/users/cdwivedi/RL_EXP/IDL/project/tgif-qa/code/dataset/tgif/gifs/" ,num_retries=100):
        """
        Construct the Kinetics video loader with a given csv file. The format of
        the csv file is:
        ```
        path_to_video_1 label_1
        path_to_video_2 label_2
        ...
        path_to_video_N label_N
        ```
        Args:
            cfg (CfgNode): configs.
            mode (string): Options includes `train`, `val`, or `test` mode.
                For the train and val mode, the data loader will take data
                from the train or val set, and sample one clip per video.
                For the test mode, the data loader will take data from test set,
                and sample multiple clips per video.
            num_retries (int): number of retries.
        """
        self.data_root = root_path
        # Only support train, val, and test mode.
        assert mode in [
            "train",
            "val",
            "test",
        ], "Split '{}' not supported for Kinetics".format(mode)
        self.mode = "test"
        self.cfg = cfg

        self._video_meta = {}
        self._num_retries = num_retries
        # For training or validation mode, one single clip is sampled from every
        # video. For testing, NUM_ENSEMBLE_VIEWS clips are sampled from every
        # video. For every clip, NUM_SPATIAL_CROPS is cropped spatially from
        # the frames.
        if self.mode in ["train", "val"]:
            self._num_clips = 1
        elif self.mode in ["test"]:
            self._num_clips = (
                cfg.TEST.NUM_ENSEMBLE_VIEWS * cfg.TEST.NUM_SPATIAL_CROPS
            )

        logger.info("Constructing Kinetics {}...".format(mode))
        self._construct_loader()

    def _construct_loader(self):
        """
        Construct the video loader.
        """
        path_to_file = "/users/cdwivedi/RL_EXP/IDL/project/tgif-qa/dataset/Train_action_question.csv"
        assert os.path.exists(path_to_file), "{} dir not found".format(
            path_to_file
        )

        self._path_to_videos = []
        self._labels = []
        self._spatial_temporal_idx = []
        count = 0
        with open(path_to_file, "r") as f:
            for clip_idx, path_label in enumerate(f.read().splitlines()):
                if count == 0:
                    count += 1
                else:
                    path = path_label.split("\t")[0]
                    label = 1
                    self._path_to_videos.append(path)
                    self._labels.append(int(label))
                    self._spatial_temporal_idx.append(0)
                    self._video_meta[clip_idx * self._num_clips +0] = {}
        assert (
            len(self._path_to_videos) > 0
        ), "Failed to load Kinetics split {} from {}".format(
            self._split_idx, path_to_file
        )
        logger.info(
            "Constructing kinetics dataloader (size: {}) from {}".format(
                len(self._path_to_videos), path_to_file
            )
        )

    def pack_pathway_output(self, frames):
        """
        Prepare output as a list of tensors. Each tensor corresponding to a
        unique pathway.
        Args:
            frames (tensor): frames of images sampled from the video. The
                dimension is `channel` x `num frames` x `height` x `width`.
        Returns:
            frame_list (list): list of tensors with the dimension of
                `channel` x `num frames` x `height` x `width`.
        """
        if self.cfg.MODEL.ARCH in self.cfg.MODEL.SINGLE_PATHWAY_ARCH:
            frame_list = [frames]
        elif self.cfg.MODEL.ARCH in self.cfg.MODEL.MULTI_PATHWAY_ARCH:
            fast_pathway = frames
            # Perform temporal sampling from the fast pathway.
            slow_pathway = torch.index_select(
                frames,
                1,
                torch.linspace(
                    0,
                    frames.shape[1] - 1,
                    frames.shape[1] // self.cfg.SLOWFAST.ALPHA,
                ).long(),
            )
            frame_list = [slow_pathway, fast_pathway]
        else:
            raise NotImplementedError(
                "Model arch {} is not in {}".format(
                    self.cfg.MODEL.ARCH,
                    self.cfg.MODEL.SINGLE_PATHWAY_ARCH
                    + self.cfg.MODEL.MULTI_PATHWAY_ARCH,
                )
            )
        return frame_list
    
    def getFrames(self, path, max_frames = 32, img_h = 256, img_w = 256):
        path = path.split(".")[0] + ".gi"
        #print(self.data_root + path +"/*")
        files = glob.glob(self.data_root + path +"/*")
        frame_idx = torch.arange(len(files), requires_grad=False)
        start_idx, end_idx= 0, len(frame_idx)
        num_samples = max_frames
        index = torch.linspace(start_idx, end_idx, num_samples)
        index = torch.clamp(index, 0, frame_idx.shape[0] - 1).long()
        frame_idx = torch.index_select(frame_idx, 0, index)
        frames = []
        for idx in frame_idx:
            image = Image.open(files[idx])
            #image = TF.resize(image, (img_h, img_w))
            x = TF.to_tensor(image).unsqueeze(0)

            frames.append(x.permute(0,2,3,1))
        frames = torch.cat(frames, dim=0)
        return frames
    
    def check_gif(self, path):
        path = self.data_root + path.split(".")[0] + ".gi"
        return os.path.isdir(path)

    def __getitem__(self, path, index = 0):
        """
        Given the video index, return the list of frames, label, and video
        index if the video can be fetched and decoded successfully, otherwise
        repeatly find a random video that can be decoded as a replacement.
        Args:
            index (int): the video index provided by the pytorch sampler.
        Returns:
            frames (tensor): the frames of sampled from the video. The dimension
                is `channel` x `num frames` x `height` x `width`.
            label (int): the label of the current video.
            index (int): if the video provided by pytorch sampler can be
                decoded, then return the index of the video. If not, return the
                index of the video replacement that can be decoded.
        """
        index = 0
        #print("HERE , ", index)
        if self.mode in ["train", "val"]:
            # -1 indicates random sampling.
            temporal_sample_index = -1
            spatial_sample_index = -1
            min_scale = self.cfg.DATA.TRAIN_JITTER_SCALES[0]
            max_scale = self.cfg.DATA.TRAIN_JITTER_SCALES[1]
            crop_size = self.cfg.DATA.TRAIN_CROP_SIZE
        elif self.mode in ["test"]:
            temporal_sample_index = (
                self._spatial_temporal_idx[index]
                // self.cfg.TEST.NUM_SPATIAL_CROPS
            )
            # spatial_sample_index is in [0, 1, 2]. Corresponding to left,
            # center, or right if width is larger than height, and top, middle,
            # or bottom if height is larger than width.
            spatial_sample_index = (
                self._spatial_temporal_idx[index]
                % self.cfg.TEST.NUM_SPATIAL_CROPS
            )
            min_scale, max_scale, crop_size = [self.cfg.DATA.TEST_CROP_SIZE] * 3
            # The testing is deterministic and no jitter should be performed.
            # min_scale, max_scale, and crop_size are expect to be the same.
            assert len({min_scale, max_scale, crop_size}) == 1
        else:
            raise NotImplementedError(
                "Does not support {} mode".format(self.mode)
            )

        # Try to decode and sample a clip from a video. If the video can not be
        # decoded, repeatly find a random video replacement that can be decoded.
        for _ in range(self._num_retries):
            try:
                frames = self.getFrames(path)
            except Exception as e:
                logger.info(
                    "Failed to load video from {} with error {}".format(
                        self._path_to_videos[index], e
                    )
                )
                print(
                    "Failed to load video from {} with error {}".format(
                        self._path_to_videos[index], e
                    )
                )
            # Select a random video if the current video was not able to access.
            #if video_container is None:
            #    index = random.randint(0, len(self._path_to_videos) - 1)
            #    continue

            # Decode video. Meta info is used to perform selective decoding.
            '''frames = decoder.decode(
                video_container,
                self.cfg.DATA.SAMPLING_RATE,
                self.cfg.DATA.NUM_FRAMES,
                temporal_sample_index,
                self.cfg.TEST.NUM_ENSEMBLE_VIEWS,
                video_meta=self._video_meta[index],
                target_fps=30,
            )'''

            # If decoding failed (wrong format, video is too short, and etc),
            # select another video.
            #if frames is None:
            #    index = random.randint(0, len(self._path_to_videos) - 1)
            #    continue

            # Perform color normalization.
            frames = frames.float()
            frames = frames / 255.0
            frames = frames - torch.tensor(self.cfg.DATA.MEAN)
            frames = frames / torch.tensor(self.cfg.DATA.STD)
            # T H W C -> C T H W.
            frames = frames.permute(3, 0, 1, 2)
            # Perform data augmentation.
            frames = self.spatial_sampling(
                frames,
                spatial_idx=spatial_sample_index,
                min_scale=min_scale,
                max_scale=max_scale,
                crop_size=crop_size,
            )

            label = self._labels[index]
            frames = self.pack_pathway_output(frames)
            return frames[0],frames[1]
        else:
            raise RuntimeError(
                "Failed to fetch video after {} retries.".format(
                    self._num_retries
                )
            )

    def __len__(self):
        """
        Returns:
            (int): the number of videos in the dataset.
        """
        return len(self._path_to_videos)

    def spatial_sampling(
        self,
        frames,
        spatial_idx=-1,
        min_scale=256,
        max_scale=320,
        crop_size=224,
    ):
        """
        Perform spatial sampling on the given video frames. If spatial_idx is
        -1, perform random scale, random crop, and random flip on the given
        frames. If spatial_idx is 0, 1, or 2, perform spatial uniform sampling
        with the given spatial_idx.
        Args:
            frames (tensor): frames of images sampled from the video. The
                dimension is `num frames` x `height` x `width` x `channel`.
            spatial_idx (int): if -1, perform random spatial sampling. If 0, 1,
                or 2, perform left, center, right crop if width is larger than
                height, and perform top, center, buttom crop if height is larger
                than width.
            min_scale (int): the minimal size of scaling.
            max_scale (int): the maximal size of scaling.
            crop_size (int): the size of height and width used to crop the
                frames.
        Returns:
            frames (tensor): spatially sampled frames.
        """
        assert spatial_idx in [-1, 0, 1, 2]
        if spatial_idx == -1:
            frames = transform.random_short_side_scale_jitter(
                frames, min_scale, max_scale
            )
            #frames = transform.random_crop(frames, crop_size)
            frames = transform.horizontal_flip(0.5, frames)
        else:
            # The testing is deterministic and no jitter should be performed.
            # min_scale, max_scale, and crop_size are expect to be the same.
            assert len({min_scale, max_scale, crop_size}) == 1
            frames = transform.random_short_side_scale_jitter(
                frames, min_scale, max_scale
            )
            frames = transform.uniform_crop(frames, crop_size, spatial_idx)
        return frames


