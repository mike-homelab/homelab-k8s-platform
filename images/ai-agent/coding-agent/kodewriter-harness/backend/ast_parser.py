from tree_sitter_languages import get_language, get_parser
from typing import List, Dict, Any

class ASTParser:
    def __init__(self, language_name: str = "python"):
        self.language = get_language(language_name)
        self.parser = get_parser(language_name)

    def extract_symbols(self, code: str) -> List[Dict[str, Any]]:
        tree = self.parser.parse(bytes(code, "utf8"))
        root_node = tree.root_node
        symbols = []
        self._traverse(root_node, symbols)
        return symbols

    def _traverse(self, node, symbols: List[Dict[str, Any]]):
        if node.type in ["function_definition", "class_definition"]:
            name_node = node.child_by_field_name("name")
            if name_node:
                symbols.append({
                    "type": node.type,
                    "name": name_node.text.decode("utf8"),
                    "start": node.start_point,
                    "end": node.end_point
                })
        for child in node.children:
            self._traverse(child, symbols)
