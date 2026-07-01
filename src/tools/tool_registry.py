"""
Tool Registry for managing tool metadata and configuration.
"""

import os
import json
import inspect
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass
from google.adk.tools import FunctionTool

@dataclass
class ToolMetadata:
    """Metadata for a single tool function"""
    name: str
    tool_name: str
    action: str
    description: str
    signature: Dict[str, Any]

class ToolRegistry:
    """Registry for managing tool metadata and creation"""
    
    def __init__(self):
        self.tools: Dict[str, Dict[str, ToolMetadata]] = {}
        self._load_metadata()
    
    def _load_metadata(self) -> None:
        """Load all tool metadata from JSON files"""
        metadata_dir = os.path.join(os.path.dirname(__file__), "metadata")
        if not os.path.exists(metadata_dir):
            raise RuntimeError(f"Metadata directory not found: {metadata_dir}")
            
        for filename in os.listdir(metadata_dir):
            if filename.endswith('.json'):
                with open(os.path.join(metadata_dir, filename), 'r') as f:
                    data = json.load(f)
                    category = data["tool_category"]
                    self.tools[category] = {}
                    
                    for tool in data["tools"]:
                        metadata = ToolMetadata(
                            name=tool["name"],
                            tool_name=tool["tool_name"],
                            action=tool["action"],
                            description=tool["description"],
                            signature=tool["signature"]
                        )
                        self.tools[category][tool["name"]] = metadata
    
    def create_tool(self, category: str, name: str, func: Callable) -> FunctionTool:
        """Create a FunctionTool with metadata from the registry"""
        if category not in self.tools or name not in self.tools[category]:
            raise ValueError(f"No metadata found for tool {category}.{name}")
            
        metadata = self.tools[category][name]
        
        # Validate function signature matches metadata
        sig = inspect.signature(func)
        meta_params = set(metadata.signature["parameters"].keys())
        func_params = set(p.name for p in sig.parameters.values())
        has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_kwargs and not meta_params.issubset(func_params):
            raise ValueError(
                f"Function signature doesn't match metadata for {name}. "
                f"Expected parameters: {meta_params}, got: {func_params}"
            )
        
        # Create tool with metadata
        tool = FunctionTool(func)
        tool.custom_metadata = {
            "tool_name": metadata.tool_name,
            "action": metadata.action
        }
        return tool
    
    def get_metadata(self, category: str, name: str) -> Optional[ToolMetadata]:
        """Get metadata for a specific tool"""
        return self.tools.get(category, {}).get(name)
    
    def list_tools(self, category: Optional[str] = None) -> List[ToolMetadata]:
        """List all tools, optionally filtered by category"""
        if category:
            return list(self.tools.get(category, {}).values())
        return [
            tool
            for category in self.tools.values()
            for tool in category.values()
        ]
    
    def get_signature(self, category: str, name: str) -> Optional[Dict[str, Any]]:
        """Get function signature for a specific tool"""
        metadata = self.get_metadata(category, name)
        return metadata.signature if metadata else None
