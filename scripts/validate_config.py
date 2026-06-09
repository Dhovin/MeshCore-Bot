import os
import sys
import json

# Add project root to path so we can import core.validator
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(script_dir, ".."))
sys.path.insert(0, project_root)

from core.validator import validate as validate_schema

def main():
    config_path = os.path.join(project_root, "config", "config.json")
    schema_path = os.path.join(project_root, "config", "schema.json")
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}")
        sys.exit(1)
        
    if not os.path.exists(schema_path):
        print(f"Error: Schema file not found at {schema_path}")
        sys.exit(1)
        
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        with open(schema_path, 'r') as f:
            schema = json.load(f)
    except Exception as e:
        print(f"Error reading/parsing config or schema: {e}")
        sys.exit(1)
        
    errors = validate_schema(schema, config)
    if errors:
        print("Configuration schema validation FAILED:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)
    else:
        print("Configuration is valid.")
        sys.exit(0)

if __name__ == '__main__':
    main()
