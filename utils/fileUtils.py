# -*- coding: utf-8 -*-
import sys
import os
import shutil
import zipfile
#import win32api


import hashlib
#from win32api import GetFileVersionInfo, LOWORD, HIWORD

parentdir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(1, parentdir)

from utils.SlicerLog import SlicerLog
logger = SlicerLog.getLogger("Slicer_Log")

class FileUtils(object):
    @staticmethod
    # Return True if file exist
    def fileExist(filePath):
        logger.info("PATH: %s" %filePath)
        return os.path.exists(filePath)

    @staticmethod
    # Retun MD5 of a file
    def md5(fname):
        try:
            hash_md5 = hashlib.md5()
            with open(fname, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.exception("Exception happens with error %s" % e)
            return None

    @staticmethod
    # Return hash of a file
    def hashOfFile(filename):
        try:
            h = hashlib.sha1()
            with open(filename, 'rb') as file:
                chunk = 0
                while chunk != b'':
                    chunk = file.read(1024)
                    h.update(chunk)
            return h.hexdigest()
        except Exception as e:
            logger.exception("Exception happens, error: %s" % e)
            return None

    @staticmethod
    # Return the line number of a file
    def getFileLineNumber(filePath):
        count = 0
        try:
            f = open(filePath, 'r', encoding='utf8', errors='ignore')
            for line in f:
                count += 1
            f.close()
            return count
        except Exception as err:
            logger.exception(str(err))
            return count

    @staticmethod
    def getFileExistence(filePath):
        return os.path.exists(filePath)

    @staticmethod
    # Return the file size (MB)
    def getFileSize(filePath):
        try:
            return os.path.getsize(filePath)/(1024*1024)
        except Exception as err:
            logger.exception(str(err))
            return None

    @staticmethod
    # Return the file size (Bytes)
    def getFileSizeinBytes(filePath):
        try:
            return os.path.getsize(filePath)
        except Exception as err:
            logger.info("Failed to get the size of " + filePath)
            return None

    @staticmethod
    # Create a folder in the path
    def createLogFolder(folderPath ,folderName):
        try:
            logPath = folderPath + '/' + folderName
            if not os.path.exists(logPath):
                os.mkdir(logPath)
        except Exception as err:
            logger.exception("Create file folder failed!")
            logger.exception(str(err))

    @staticmethod
    # Return all the files in the folder with file path list
    def getFilesInFolder(fileDir):
        filePaths = []
        try:
            for root, _, files in os.walk(fileDir):
                for file in files:
                    filePaths.append(os.path.join(root, file))
            return filePaths
        except Exception as err:
            logger.exception(str(err))
            return filePaths

    @staticmethod
    # Return file name without filetype, eg. file.txt -> file
    def removeFileType(fileName):
        try:
            return fileName.partition('.')[0]
        except Exception as err:
            logger.exception(str(err))
            return ""

    @staticmethod
    def getFileName(filePath):
        try:
            return os.path.basename(filePath)
        except Exception as err:
            logger.exception(str(err))
            return ""
    
    @staticmethod
    # Return the file type from file path
    def getFileType(filePath):
        try:
            return os.path.splitext(filePath)[-1]
        except Exception as err:
            logger.exception(str(err))
            return ""  

    @staticmethod
    # Return folder path from file path
    def getFolderPath(filePath):
        try:
            return os.path.dirname(filePath)
        except Exception as err:
            logger.exception(str(err))
            return ""  

    @staticmethod
    # return last modify time
    def getModifyTime(filePath):
        try:
            return os.path.getmtime(filePath)
        except Exception as err:
            print(str(err))
            import time
            return time.time()

    @staticmethod
    # Return True if file is a compressed files
    def isCompressedFile(filePath):
        try:
            compressedFileTypes = [".zip", ".7z", ".gz", ".tar", ".bz2"]
            for fileType in compressedFileTypes:
                if filePath.endswith(fileType):
                    return True
            return False
        except Exception as err:
            logger.exception(str(err))
            return False

    @staticmethod
    # Return True if file is a text file
    def isTextFile(filePath):
        try:
            textFileTypes = [".log", ".ini", ".reg", ".txt"]
            for fileType in textFileTypes:
                if filePath.endswith(fileType):
                    return True
            return False
        except Exception as err:
            logger.exception(str(err))
            return False

    @staticmethod
    # CompressedFile
    def CompressedFile(zipfilename,filePath):
        z = zipfile.ZipFile(zipfilename, 'w', zipfile.ZIP_DEFLATED)
        startdir = filePath
        for dirpath, dirnames, filenames in os.walk(startdir):
            for filename in filenames:
                z.write(os.path.join(dirpath, filename),arcname = filename)
        z.close()

    @staticmethod
    # CopyFile
    def Collectfile(srcpath,filePath):
        try:
            shutil.copyfile(srcpath, filePath)
        except Exception as err:
            logger.info(str(err))  

    @staticmethod
    # Delete the entire folder
    def deleteFolder(folderPath):
        try:
            shutil.rmtree(folderPath, ignore_errors=True)
        except Exception as err:
            logger.exception(str(err))    
            return

    @staticmethod
    # Delete the files
    def deleteFiles(filePaths):
        for file in filePaths:
            try:
                if file == "":
                    continue
                os.remove(file)
            except Exception as err:
                logger.exception(str(err))



    @staticmethod
    # detect if OS is Windows
    def isWindows():
        try:
            if os.path.exists(os.getenv("SystemDrive") + r"\Program Files"):
                return True
            else:
                return False
        except Exception as err:
             logger.exception(str(err))


    @staticmethod
    def copyFolder(src, dst, symlinks = False, ignore = None):
        try:
            import stat
            if not os.path.exists(dst):
                os.makedirs(dst)
                shutil.copystat(src, dst)
            lst = os.listdir(src)
            if ignore:
                excl = ignore(src, lst)
                lst = [x for x in lst if x not in excl]
            for item in lst:
                s = os.path.join(src, item)
                d = os.path.join(dst, item)
                if symlinks and os.path.islink(s):
                    if os.path.lexists(d):
                        os.remove(d)
                    os.symlink(os.readlink(s), d)
                    try:
                        st = os.lstat(s)
                        mode = stat.S_IMODE(st.st_mode) 
                        os.lchmod(d, mode)
                    except:
                        pass # lchmod not available
                elif os.path.isdir(s):
                    FileUtils.copyFolder(s, d, symlinks, ignore)
                else:
                    shutil.copy2(s, d)
        except Exception as err:
            logger.exception(str(err))

    @staticmethod
    def copyTree(src, dst, symlinks=False, ignore=None):
        try:
            for item in os.listdir(src):
                s = os.path.join(src, item)
                d = os.path.join(dst, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, symlinks, ignore)
                else:
                    shutil.copy2(s, d)
        except Exception as err:
            logger.exception(str(err))

    @staticmethod
    def getFileProperties(fname):
        """
        Read all properties of the given file return them as a dictionary.
        """
        propNames = ('Comments', 'InternalName', 'ProductName',
            'CompanyName', 'LegalCopyright', 'ProductVersion',
            'FileDescription', 'LegalTrademarks', 'PrivateBuild',
            'FileVersion', 'OriginalFilename', 'SpecialBuild')
    
        props = {'FixedFileInfo': None, 'StringFileInfo': None, 'FileVersion': None}
    
        try:
            # backslash as parm returns dictionary of numeric info corresponding to VS_FIXEDFILEINFO struc
            fixedInfo = win32api.GetFileVersionInfo(fname, '\\')
            props['FixedFileInfo'] = fixedInfo
            props['FileVersion'] = "%d.%d.%d.%d" % (fixedInfo['FileVersionMS'] / 65536,
                    fixedInfo['FileVersionMS'] % 65536, fixedInfo['FileVersionLS'] / 65536,
                    fixedInfo['FileVersionLS'] % 65536)
    
            # \VarFileInfo\Translation returns list of available (language, codepage)
            # pairs that can be used to retreive string info. We are using only the first pair.
            lang, codepage = win32api.GetFileVersionInfo(fname, '\\VarFileInfo\\Translation')[0]
    
            # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
            # two are language/codepage pair returned from above
    
            strInfo = {}
            for propName in propNames:
                strInfoPath = u'\\StringFileInfo\\%04X%04X\\%s' % (lang, codepage, propName)
                ## print str_info
                strInfo[propName] = win32api.GetFileVersionInfo(fname, strInfoPath)
    
            props['StringFileInfo'] = strInfo
        except Exception as err:
            logger.exception(str(err))
        return props

    @staticmethod
    def downloadFileFromURL(url):
        try:
            get_response = requests.get(url,stream=True)
            #print(get_response.headers)
            #print(type(get_response.headers.get('Content-Length')))
            file_name  = url.split("/")[-1]
            with open(file_name, 'wb') as f:
                for chunk in get_response.iter_content(chunk_size=1024):
                    if chunk: # filter out keep-alive new chunks
                        #print( len(chunk))
                        f.write(chunk)
        except Exception as err:
            logger.exception("downloadFileFromURL() : %s" % str(err))

    @staticmethod
    def rename_file_extension_old(src_path: str):
        '''
        if file exists, rename it by appending .old to filename
        '''
        try:
            dst_path = src_path + ".old"
            if os.path.exists(src_path):
                if os.path.exists(dst_path):
                    os.remove(dst_path)
                    os.rename(src_path, dst_path)
            return
        except Exception as err:
            logger.error('rename_file_extension_old: ' + str(err))

if __name__ == "__main__":
    path = "requirements.txt"
    import time
    timestr = FileUtils.getModifyTime(path)
    print(timestr)
    print(time.time())