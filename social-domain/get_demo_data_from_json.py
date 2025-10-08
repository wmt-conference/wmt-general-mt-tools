#!/usr/bin/python
import argparse
import json
import requests
from pathlib import Path
from urllib.parse import urlparse
import re

def main(args):
    # simulate the sampled data by downloading the mastodon data
    with open(args.demo_data_file, "r", encoding="utf-8") as f:
        threads = json.load(f)

    download_threads(threads, Path(args.output_dir))


def get_mastodon_post_by_id(instance: str, post_id: int) -> dict:
    url = f"https://{instance}/api/v1/statuses/{post_id}"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()


def get_mastodon_post_from_uri(uri: str) -> dict:
    parsed = urlparse(uri)
    instance = parsed.netloc
    # extract numeric ID at the end of the path
    match = re.search(r"/statuses/(\d+)$", parsed.path)
    if not match:
        raise ValueError(f"Could not extract status id from {uri}")
    status_id = match.group(1)

    api_url = f"https://{instance}/api/v1/statuses/{status_id}"
    r = requests.get(api_url)
    r.raise_for_status()
    return r.json()

def download_threads(threads_by_lang: dict, out_dir: Path):
    """
    Download threads from Mastodon by URI and save as JSONL files.
    Files are placed in subfolders by language.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for lang, threads in threads_by_lang.items():
        lang_dir = out_dir / lang
        lang_dir.mkdir(parents=True, exist_ok=True)

        for thread in threads:
            thread_id = thread["thread_id"]
            uris = thread["post_uris"]   # <- URIs, not IDs

            out_file = lang_dir / f"{thread_id}.jsonl"
            with open(out_file, "w", encoding="utf-8") as f:
                for uri in uris:
                    try:
                        post = get_mastodon_post_from_uri(uri)
                    except Exception as e:
                        print(f"Failed to fetch {uri}: {e}")
                        continue
                    f.write(json.dumps(post, ensure_ascii=False) + "\n")
            print(f"Saved {out_file}")
        
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download Mastodon threads and save as .jsonl files organised by language."
    )
    parser.add_argument(
        "demo_data_file",
        help=("Path to JSON file with structure like "
          "{'en': [{'thread_id': ..., 'post_uris': [...]}, ...], "
          " 'ja': [...]}."
          )
    )
    parser.add_argument(
        "output_dir",
        help="Top-level output directory. Language subfolders will be created automatically."
    )
    args = parser.parse_args()

    main(args)
