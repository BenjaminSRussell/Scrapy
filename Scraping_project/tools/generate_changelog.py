"""
This script generates a CHANGELOG.md file from git commit messages.
"""
import subprocess
import re
from collections import defaultdict

def get_commit_messages():
    """Fetches commit messages from the git repository."""
    try:
        output = subprocess.check_output(["git", "log", "--pretty=format:%s"]).decode("utf-8")
        return output.split('\n')
    except subprocess.CalledProcessError:
        return []

def generate_changelog():
    """Generates a CHANGELOG.md file from commit messages."""
    commit_messages = get_commit_messages()
    if not commit_messages:
        print("No commit messages found.")
        return

    categorized_messages = defaultdict(list)
    for message in commit_messages:
        match = re.match(r"^(feat|fix|docs|refactor):\s*(.*)", message)
        if match:
            category, summary = match.groups()
            categorized_messages[category].append(summary)

    with open("CHANGELOG.md", "w") as f:
        f.write("# Changelog\n\n")
        if categorized_messages["feat"]:
            f.write("## Features\n\n")
            for summary in categorized_messages["feat"]:
                f.write(f"- {summary}\n")
            f.write("\n")

        if categorized_messages["fix"]:
            f.write("## Bug Fixes\n\n")
            for summary in categorized_messages["fix"]:
                f.write(f"- {summary}\n")
            f.write("\n")

        if categorized_messages["docs"]:
            f.write("## Documentation\n\n")
            for summary in categorized_messages["docs"]:
                f.write(f"- {summary}\n")
            f.write("\n")

        if categorized_messages["refactor"]:
            f.write("## Refactoring\n\n")
            for summary in categorized_messages["refactor"]:
                f.write(f"- {summary}\n")
            f.write("\n")

    print("CHANGELOG.md generated successfully.")

if __name__ == "__main__":
    generate_changelog()
