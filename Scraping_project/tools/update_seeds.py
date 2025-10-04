
import json
import os

import pandas as pd


def is_valid_url(url):
    """Checks if a URL is valid and not a mailto link."""
    return url.startswith(('http://', 'https://'))

def update_seed_urls(project_root, validation_output_path, seed_path, min_successful_validations=3):
    """
    Updates the seed URL file with high-quality URLs from the validation output.

    Args:
        project_root (str): The root directory of the Scrapy project.
        validation_output_path (str): The path to the validation_output.jsonl file.
        seed_path (str): The path to the uconn_urls.csv seed file.
        min_successful_validations (int): The minimum number of successful validations for a URL to be considered high-quality.
    """
    validation_output_file = os.path.join(project_root, validation_output_path)
    seed_file = os.path.join(project_root, seed_path)

    if not os.path.exists(validation_output_file):
        print(f"Error: Validation output file not found at {validation_output_file}")
        return

    # Read validation output
    with open(validation_output_file) as f:
        validation_data = [json.loads(line) for line in f]

    # Filter for high-quality URLs (using status_code, not status)
    successful_validations = [
        item['url'] for item in validation_data
        if item.get('status_code') == 200 and item.get('content_type', '').startswith('text/html') and is_valid_url(item['url'])
    ]

    # Count successful validations for each URL
    url_counts = pd.Series(successful_validations).value_counts()

    # Get URLs that meet the minimum validation count
    high_quality_urls = url_counts[url_counts >= min_successful_validations].index.tolist()

    if not high_quality_urls:
        print("No new high-quality URLs found to update the seed file.")
        return

    # Read existing seed URLs
    if os.path.exists(seed_file):
        existing_seeds = pd.read_csv(seed_file, header=None)[0].tolist()
    else:
        existing_seeds = []

    # Add new URLs, avoiding duplicates
    new_seeds = set(existing_seeds)
    for url in high_quality_urls:
        if url not in new_seeds:
            new_seeds.add(url)

    # Write updated seeds to the file
    pd.DataFrame(list(new_seeds)).to_csv(seed_file, header=False, index=False)

    print(f"Successfully updated seed file with {len(new_seeds) - len(existing_seeds)} new URLs.")
    print(f"Seed file located at: {seed_file}")

if __name__ == '__main__':
    # This assumes the script is run from the 'tools' directory
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    validation_output_path = os.path.join('data', 'processed', 'stage02', 'validation_output.jsonl')
    seed_path = os.path.join('data', 'raw', 'uconn_urls.csv')
    update_seed_urls(project_root, validation_output_path, seed_path)
