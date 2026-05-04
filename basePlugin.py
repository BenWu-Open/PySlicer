import sys
import os
import time

from utils.SlicerLog import SlicerLog
logger = SlicerLog.getLogger("Slicer_Log")

class basePlugin:

    def __init__(self, context=None):
        try:
            self.name = "basePlugin Name"
            self.detail = "Detail: This is the basePlugin."

            self.log__Queue =  None
            self.Queue__FromParent = None
            self.Queue__FromChild = None
            self.event__ParentSent = None
            self.event__ChildSent = None

        except Exception as err:
            logger.exception(str(err))
    
    def __del__(self):
        pass

    def setup__Plugin (self, logQueue, QueueFromParent, QueueFromChild, eventParentSent, eventChildSent):
        try:
            self.log__Queue =  logQueue
            self.Queue__FromParent = QueueFromParent
            self.Queue__FromChild = QueueFromChild
            self.event__ParentSent = eventParentSent
            self.event__ChildSent = eventChildSent

            
        except Exception as e:
            logger.error(str(e))
            return






    



