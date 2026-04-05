import abc
import json
import logging
from typing import List, Dict, Any
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

class IOutputChannel(abc.ABC):
    @abc.abstractmethod
    def emit(self, target_account: str, processed_results: List[Dict[str, Any]]) -> bool:
        """
        Emits the processed NLP results.
        Returns True if successful (which allows Cursor to atomic update), False otherwise.
        """
        pass

class ConsoleOutputChannel(IOutputChannel):
    def __init__(self, template_dir: str = "templates", template_name: str = "console_output.j2"):
        self.template_dir = Path(template_dir)
        self.template_name = template_name
        
        # Load Jinja Environment
        if self.template_dir.exists():
            self.env = Environment(loader=FileSystemLoader(str(self.template_dir)))
        else:
            self.env = None
            logger.warning(f"Template directory {template_dir} not found. Falling back to raw JSON format.")

    def emit(self, target_account: str, processed_results: List[Dict[str, Any]]) -> bool:
        if not processed_results:
            return True

        logger.info(f"Emitting {len(processed_results)} parsed results for {target_account}...")
        
        for result in processed_results:
            if self.env:
                try:
                    template = self.env.get_template(self.template_name)
                    rendered = template.render(**result)
                    print(rendered)
                except Exception as e:
                    logger.error(f"Failed to render template for UID {result.get('original_uid')}: {e}")
                    print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                # Raw JSON fallback
                print(json.dumps(result, indent=2, ensure_ascii=False))

        # Always return True for stateless local output
        return True

class FileOutputChannel(IOutputChannel):
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def emit(self, target_account: str, processed_results: List[Dict[str, Any]]) -> bool:
        if not processed_results:
            return True

        logger.info(f"Writing {len(processed_results)} results to {self.file_path} for {target_account}...")
        
        try:
            # We append or write array. We'll write/overwrite for run_once scope.
            # To handle multiple accounts nicely, we merge into a top-level obj if needed, 
            # but usually single target runs or append works. Let's just write raw JSON.
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(processed_results, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to write to file {self.file_path}: {e}")
            return False
