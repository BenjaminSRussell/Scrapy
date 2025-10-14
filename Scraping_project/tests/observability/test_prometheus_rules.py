import yaml
import sys
import os

def validate_prometheus_rules(directory):
    """
    Validates the structure of Prometheus or Grafana rule files in a directory.
    This is a basic fallback validator for when promtool is not available.
    """
    all_valid = True
    for filename in os.listdir(directory):
        if filename.endswith(".yml") or filename.endswith(".yaml"):
            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, 'r') as f:
                    data = yaml.safe_load(f)
            except (FileNotFoundError, yaml.YAMLError) as e:
                print(f"Error parsing {filepath}: {e}", file=sys.stderr)
                all_valid = False
                continue

            if 'groups' not in data or not isinstance(data['groups'], list):
                print(f"Validation Error in {filepath}: 'groups' key not found or is not a list.", file=sys.stderr)
                all_valid = False
                continue

            for i, group in enumerate(data['groups']):
                if 'name' not in group:
                    print(f"Validation Error in {filepath}, group {i}: 'name' is a required field.", file=sys.stderr)
                    all_valid = False
                if 'rules' not in group or not isinstance(group['rules'], list):
                    print(f"Validation Error in {filepath}, group {i}: 'rules' key not found or is not a list.", file=sys.stderr)
                    all_valid = False
                    continue
                for j, rule in enumerate(group['rules']):
                    is_prometheus_rule = ('alert' in rule or 'record' in rule) and 'expr' in rule
                    is_grafana_rule = 'title' in rule and 'condition' in rule and 'data' in rule

                    if not (is_prometheus_rule or is_grafana_rule):
                        print(f"Validation Error in {filepath}, group {i}, rule {j}: Rule must be a valid Prometheus or Grafana alert rule.", file=sys.stderr)
                        all_valid = False
                        continue

                    if is_grafana_rule:
                        if not isinstance(rule.get('data'), list) or not rule.get('data'):
                            print(f"Validation Error in {filepath}, group {i}, rule {j}: Grafana rule 'data' must be a non-empty list.", file=sys.stderr)
                            all_valid = False
                        else:
                            has_expr = False
                            for data_item in rule.get('data', []):
                                if 'model' in data_item and 'expr' in data_item.get('model', {}):
                                    has_expr = True
                                    break
                            if not has_expr:
                                print(f"Validation Error in {filepath}, group {i}, rule {j}: No 'expr' found in any data model for Grafana rule.", file=sys.stderr)
                                all_valid = False

    return all_valid

if __name__ == "__main__":
    dir_to_validate = "Scraping_project/monitoring/alerting/"
    if validate_prometheus_rules(dir_to_validate):
        print("Prometheus rule validation successful.")
        sys.exit(0)
    else:
        print("Prometheus rule validation failed.")
        sys.exit(1)
