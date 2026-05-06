#!/usr/bin/env python3
import arxiv
import os
import time
import requests
import urllib.parse
import re
import yaml
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Try to import PyMed
try:
    from pymed import PubMed
except ImportError:
    logger.warning("PyMed not installed. PubMed hunting disabled. Run 'pip install pymed'")
    PubMed = None

def load_config():
    # Try to find config.yaml in the root directory
    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not config_path.exists():
        config_path = Path("config.yaml")
    
    if not config_path.exists():
        logger.error(f"Config file not found at {config_path}")
        return {}
        
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def title_to_filename(title: str) -> str:
    """Follow the project's naming convention for Obsidian notes"""
    filename = re.sub(r'[ /\\:*?"<>|]+', '_', title).strip('_')
    return filename

CONFIG = load_config()
VAULT_PATH = Path(CONFIG.get("vault_path", "./vault")).expanduser()
PAPERS_DIR = VAULT_PATH / CONFIG.get("papers_dir", "Papers")
PDF_DIR = PAPERS_DIR / "PDFs"

def ensure_dirs():
    if not PAPERS_DIR.exists():
        logger.info(f"Creating papers directory: {PAPERS_DIR}")
        PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    if not PDF_DIR.exists():
        logger.info(f"Creating PDF directory: {PDF_DIR}")
        PDF_DIR.mkdir(parents=True, exist_ok=True)

def get_existing_identifiers():
    """Scan existing files to prevent duplicates"""
    ids = set()
    if not PAPERS_DIR.exists():
        return ids
    for f in PAPERS_DIR.glob("*.md"):
        try:
            content = f.read_text(encoding="utf-8")
            # Extract DOI
            doi_match = re.search(r'doi:\s*(.*)', content)
            if doi_match: ids.add(doi_match.group(1).strip().lower())
            # Extract Title from metadata
            title_match = re.search(r'title:\s*"(.*)"', content)
            if title_match: ids.add(title_match.group(1).strip().lower())
        except: continue
    return ids

def save_paper(paper_data: dict):
    """Save paper to the vault"""
    ensure_dirs()
    
    clean_title = title_to_filename(paper_data['title'])
    
    # Check for duplicates
    existing = get_existing_identifiers()
    if paper_data.get('doi') and paper_data['doi'].lower() in existing: return False
    if paper_data['title'].lower() in existing: return False

    file_path = PAPERS_DIR / f"{clean_title}.md"
    if file_path.exists(): return False

    # PDF Processing
    pdf_filename = f"{clean_title}.pdf"
    pdf_path = PDF_DIR / pdf_filename
    
    downloaded = False
    if paper_data.get('pdf_url') and not pdf_path.exists():
        logger.info(f"Downloading PDF from {paper_data['source']}: {paper_data['title'][:50]}...")
        try:
            r = requests.get(paper_data['pdf_url'], timeout=30)
            if r.status_code == 200 and b'%PDF' in r.content[:100]:
                pdf_path.write_bytes(r.content)
                downloaded = True
        except Exception as e:
            logger.error(f"Download failed: {e}")

    # Generate Markdown content
    metadata = {
        "title": f'"{paper_data["title"]}"',
        "authors": f'"{paper_data["authors"]}"',
        "date": paper_data['date'],
        "source": paper_data['source'],
        "doi": paper_data.get('doi', ''),
        "url": paper_data['url'],
        "tags": ["Paper", "Auto-Hunted", paper_data['source']]
    }

    yaml_header = "---\n" + "\n".join([f"{k}: {v}" for k, v in metadata.items()]) + "\n---\n"
    
    # Use internal links for Obsidian
    pdf_link = f"[[PDFs/{pdf_filename}]]" if downloaded else "Not Available"
    
    content = f"""{yaml_header}

# Abstract
{paper_data['abstract']}

# Metadata
- **Source**: {paper_data['source']}
- **Published**: {paper_data['date']}
- **DOI**: {paper_data.get('doi', 'N/A')}
- **URL**: [{paper_data['url']}]({paper_data['url']})
- **Local PDF**: {pdf_link}

# Action
- [ ] Read and analyze
"""
    file_path.write_text(content, encoding="utf-8")
    return True

def hunt_arxiv(query: str, max_results: int):
    """Fetch from arXiv"""
    logger.info(f"Hunting on arXiv for: {query[:50]}...")
    client = arxiv.Client(page_size=max_results, delay_seconds=3, num_retries=3)
    search = arxiv.Search(query=query, max_results=max_results, sort_by=arxiv.SortCriterion.SubmittedDate)
    
    count = 0
    for result in client.results(search):
        data = {
            "title": result.title,
            "authors": ", ".join([a.name for a in result.authors]),
            "date": str(result.published.date()),
            "abstract": result.summary,
            "url": result.entry_id,
            "pdf_url": result.pdf_url,
            "source": "arXiv",
            "doi": result.doi if result.doi else ""
        }
        if save_paper(data): count += 1
    return count

def hunt_pubmed(query: str, max_results: int):
    """Fetch from PubMed"""
    if not PubMed: return 0
    logger.info(f"Hunting on PubMed for: {query[:50]}...")
    pubmed = PubMed(tool="Evil Arxiv Hunter", email=CONFIG.get("pubmed_email", "user@example.com"))
    
    # Simplify query for PubMed
    pubmed_query = query.replace('abs:', '')
    
    count = 0
    results = pubmed.query(pubmed_query, max_results=max_results)
    for article in results:
        doi = ""
        if hasattr(article, 'doi') and article.doi:
            doi = str(article.doi).split('\n')[0].strip()
            
        pmid = ""
        if hasattr(article, 'pubmed_id') and article.pubmed_id:
            pmid = str(article.pubmed_id).split('\n')[0].strip()
        
        data = {
            "title": article.title,
            "authors": ", ".join([f"{a.get('lastname','')} {a.get('firstname','')}" for a in article.authors]) if hasattr(article, 'authors') else "Unknown",
            "date": str(article.publication_date) if hasattr(article, 'publication_date') else "Unknown",
            "abstract": article.abstract if hasattr(article, 'abstract') else "No abstract",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            "pdf_url": "", 
            "source": "PubMed",
            "doi": doi
        }
        if save_paper(data): count += 1
        time.sleep(1)
    return count

def hunt_papers(source: str = "all", max_results: int = 5):
    """Main Scheduler"""
    domains = CONFIG.get("research_domains", {})
    keywords = []
    for domain, details in domains.items():
        keywords.extend(details.get("keywords", []))
    
    if not keywords:
        logger.warning("No keywords found in config.yaml. Using defaults.")
        keywords = ["large language model", "embodied AI"]

    # Construct Arxiv query
    query = " OR ".join([f'abs:"{k}"' for k in keywords[:5]]) 

    total = 0
    if source in ["arxiv", "all"]:
        total += hunt_arxiv(query, max_results)
    
    if source in ["pubmed", "all"]:
        total += hunt_pubmed(query, max_results)

    logger.info(f"Success! Hunted {total} new papers.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Multi-source paper hunter for arXiv and PubMed")
    parser.add_argument("--source", default="all", help="arxiv, pubmed, or all")
    parser.add_argument("--max_results", type=int, default=5, help="Number of papers per source")
    args = parser.parse_args()
    hunt_papers(source=args.source, max_results=args.max_results)
