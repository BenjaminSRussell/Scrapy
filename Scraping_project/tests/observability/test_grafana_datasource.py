import yaml
import sys

def validate_grafana_datasource(file_path):
    """
    Validates the Grafana datasource file.
    Ensures that all Prometheus datasources have the required fields.
    """
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: Datasource file not found at {file_path}", file=sys.stderr)
        return False
    except yaml.YAMLError as e:
        print(f"Error parsing YAML file: {e}", file=sys.stderr)
        return False

    if 'datasources' not in data or not isinstance(data['datasources'], list):
        print("Error: 'datasources' key not found or is not a list in the YAML file.", file=sys.stderr)
        return False

    all_valid = True
    for i, datasource in enumerate(data['datasources']):
        if datasource.get('type') == 'prometheus':
            name = datasource.get('name', f"Unnamed Prometheus datasource at index {i}")
            if datasource.get('access') != 'proxy':
                print(f"Validation Error in '{name}': 'access' must be 'proxy'.", file=sys.stderr)
                all_valid = False
            if 'url' not in datasource:
                print(f"Validation Error in '{name}': 'url' is a required field.", file=sys.stderr)
                all_valid = False

    return all_valid

if __name__ == "__main__":
    file_to_validate = "Scraping_project/monitoring/grafana_datasource.yml"
    if validate_grafana_datasource(file_to_validate):
        print("Grafana datasource validation successful.")
        sys.exit(0)
    else:
        print("Grafana datasource validation failed.")
        sys.exit(1)
