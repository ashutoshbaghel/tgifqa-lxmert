import os
import glob

class logger(object):
    def __init__(self, path):
        self.path = os.path.join(os.getcwd(), "log/" + path + ".log")
        
    def refresh(self):
        files = glob.glob(self.path.split(".")[0]+"*")
        for file in files:
            os.remove(file)
        
    def log(self, train_loss, train_acc, epoch):
        result_init = "\n\nEpoch: {} \n".format(epoch)
        result_string_train = "train loss: {} | train acc: {}\n".format(train_loss, train_acc)
       
        result_string = result_init+ result_string_train
        with open(self.path, "a+") as file:
            file.write(result_string)
        