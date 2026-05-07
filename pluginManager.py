# -*- coding: utf-8 -*-
import os
import sys
import re
import logging
import importlib
import inspect
import multiprocessing
from logging.handlers import QueueHandler, QueueListener

# Assuming these exist in your project structure
from basePlugin import basePlugin
from utils.fileUtils import FileUtils
from utils.SlicerLog import SlicerLog

logger = SlicerLog.getLogger("Slicer_Log")

class pluginManager:
    def __init__(self):
        self.plugins = {}  # Stores plugin metadata
        self.logger_queue = multiprocessing.Queue()
        
        # Setup QueueListener to route logs from child processes back to the main logger
        self.queue_listener = QueueListener(self.logger_queue, *logger.handlers)
        self.queue_listener.start()
        
        # Inter-process communication tools
        self.result_queue = multiprocessing.Queue()
        self.queue_to_child = multiprocessing.Queue()
        self.queue_from_child = multiprocessing.Queue()
        self.event_parent_sent = multiprocessing.Event()
        self.event_child_sent = multiprocessing.Event()
        self.event_child_sent_finish = multiprocessing.Event()

    def __del__(self):
        self.queue_listener.stop()

    @staticmethod
    def get_module_class(module_name):
        """Dynamically imports a module and returns the basePlugin subclass."""
        try:
            # Ensure the module is fresh or loaded correctly
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            current_module = importlib.import_module(module_name)
            
            for name, obj in inspect.getmembers(current_module, inspect.isclass):
                if issubclass(obj, basePlugin) and obj is not basePlugin:
                    return obj
        except Exception as err:
            logger.error(f"Failed to load module {module_name}: {err}")
        return None

    def GetPluginName(self, filePath):
        # Regex 1: Looks for the class definition inheriting from basePlugin
        # Regex 2: Captures the value inside quotes for self.name
        #class_pattern = re.compile(r"class\s+.*\bbasePlugin\b.*:")
        class_pattern = re.compile(r"class\s+[\w\d_]+\s*\([^)]*\bbasePlugin\b[^)]*\)\s*:")
        #name_pattern = re.compile(r"self\.name\s*=\s*['\"]([^'\"]+)['\"]")
        name_pattern = re.compile(r"self\.name\s*=\s*['\"](.*?)['\"]")
        
        found_base_class = False
        try:
            with open(filePath, "r", encoding="utf-8") as f:
                for line in f:
                    if not found_base_class:
                        if class_pattern.search(line):
                            found_base_class = True
                    else:
                        match = name_pattern.search(line)
                        if match:
                            return match.group(1) # Return immediately
        except Exception as err:
            logger.error(f"Error parsing {filePath}: {err}")
        
        return ""

    def getPlugins(self, plugins_dir=None):
        """Scans the plugins folder and maps module paths."""
        file_paths = []
        if not plugins_dir:
            bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            plugins_dir = os.path.join(bundle_dir, "plugins")
            file_paths = FileUtils.listFiles(plugins_dir)

            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
                local_plugins_dir = os.path.join(exe_dir, "plugins")
                file_paths = file_paths + FileUtils.listFiles(local_plugins_dir)
            
        # Add plugins parent to sys.path so importlib can find them
        #parent_of_plugins = os.path.dirname(plugins_dir)
        #if parent_of_plugins not in sys.path:
        #    sys.path.insert(0, parent_of_plugins)

        try:
            for filepath in file_paths:
                if not filepath.endswith(".py") or filepath.endswith("__init__.py"):
                    continue

                module_name = os.path.splitext(os.path.basename(filepath))[0]
                # Assuming structure: project/plugins/plugin_file.py -> plugins.plugin_file
                module_path = f"plugins.{module_name}" 
                
                plugin_display_name = self.GetPluginName(filepath)
                
                if plugin_display_name:
                    self.plugins[plugin_display_name] = {
                        "modulePath": module_path, 
                        "filePath": filepath
                    }
                    logger.info(f"Registered plugin: {plugin_display_name}")
                else:
                    logger.warning(f"No valid class found in {filepath}")
        except Exception as err:
            logger.exception(f"Plugin scanning failed: {err}")

# --- Global Execution Function (Target for Multiprocessing) ---

# UPDATE: Add plugin_context to parameters
def execute_script(logger_queue, result_queue, q_to_child, q_from_child, 
                   ev_parent, ev_child, ev_finish, module_display_name, plugins_dict, plugin_context):
    """
    Entry point for the child process.
    """
    # Configure child logging to send records to the parent's queue
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = QueueHandler(logger_queue)
    root.addHandler(handler)

    try:
        # 1. Find plugin metadata
        plugin_info = None
        for name, info in plugins_dict.items():
            if name.lower() == module_display_name.lower():
                plugin_info = info
                break
        
        if not plugin_info:
            logging.error(f"Plugin [{module_display_name}] not found in registry.")
            return

        # 2. Import and Instantiate
        module_class = pluginManager.get_module_class(plugin_info["modulePath"])
        if module_class:
            # UPDATE: Pass plugin_context into the class constructor
            instance = module_class(plugin_context)
            logging.info(f"Executing: {instance.name}")

            # 3. Setup and Run
            # Safely call setup__Plugin if it exists in your basePlugin
            if hasattr(instance, "setup__Plugin"):
                instance.setup__Plugin(result_queue, q_to_child, q_from_child, ev_parent, ev_child)
                
            instance.run()
            
            logging.info(f"Finished: {instance.name}")
        else:
            logging.error(f"Could not load class from {plugin_info['modulePath']}")

    except Exception as err:
        logging.exception(f"Child Process Error: {err}")
    finally:
        ev_finish.set()
if __name__ == "__main__":
    manager = pluginManager()
    manager.get_plugins()
    
    # Example of how to start the child process:
    # p = multiprocessing.Process(target=execute_script, args=(...))
    # p.start()

    '''
    # Inside your main application logic:
    plugin_to_run = "MyPluginName"
    process = multiprocessing.Process(
        target=execute_script,
        args=(
            manager.logger_queue,
            manager.result_queue,
            manager.queue_to_child,
            manager.queue_from_child,
            manager.event_parent_sent,
            manager.event_child_sent,
            manager.event_child_sent_finish,
            plugin_to_run,
            manager.plugins
        )
    )
    process.start()
    '''