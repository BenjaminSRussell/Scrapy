"""Entity Summarization Pipeline Demo

This script demonstrates the complete entity-centric summarization pipeline
with realistic sample data. It shows all three phases in action:

1. Extractive fact aggregation and semantic deduplication
2. Chronological sorting and abstractive summarization
3. Structured storage in Delta Lake

Usage:
    python examples/entity_summarization_demo.py
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.stage4.entity_summarization import (
    AbstractiveSummarizer,
    ChronologicalSorter,
    FactAggregator,
    Stage4EntityWorker,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def demo_fact_aggregator():
    """Demonstrate Phase 1: Fact Aggregation and Deduplication."""
    print("\n" + "="*80)
    print("PHASE 1: EXTRACTIVE FACT AGGREGATION AND DEDUPLICATION")
    print("="*80 + "\n")

    # Initialize aggregator
    aggregator = FactAggregator(
        embedding_model_name="sentence-transformers/all-MiniLM-L6-v2",
        similarity_threshold=0.85,
    )

    # Sample documents with some redundant information
    print("Adding documents to aggregator...\n")

    # Document 1: Faculty profile page
    aggregator.add_document(
        entity_name="Professor Jane Doe",
        entity_type="person",
        content="""
        Professor Jane Doe joined the University of Connecticut in 2020 as an Associate
        Professor of Computer Science. She leads the Artificial Intelligence Research Lab,
        focusing on machine learning and neural networks. Professor Doe received her PhD
        from MIT in 2015. Prior to joining UConn, she was a senior researcher at Google AI.
        """,
        source_url="https://uconn.edu/faculty/jane-doe",
        publication_date="2020-08-15",
    )

    # Document 2: Award announcement (contains duplicate info about joining UConn)
    aggregator.add_document(
        entity_name="Professor Jane Doe",
        entity_type="person",
        content="""
        Dr. Jane Doe, who joined UConn in 2020, has been awarded the prestigious NSF CAREER
        Award for her groundbreaking work in deep learning. The award provides $500,000 in
        funding over five years. Professor Doe's research focuses on improving the
        interpretability of neural networks.
        """,
        source_url="https://uconn.edu/news/jane-doe-nsf-award",
        publication_date="2021-03-10",
    )

    # Document 3: Recent publication announcement
    aggregator.add_document(
        entity_name="Professor Jane Doe",
        entity_type="person",
        content="""
        Professor Jane Doe and her team published a groundbreaking paper in Nature titled
        "Self-Explaining Neural Networks." The paper introduces a novel architecture that
        provides human-readable explanations for model predictions. This work was featured
        on the cover of Nature's March 2023 issue. Dr. Doe stated that this research could
        revolutionize AI transparency.
        """,
        source_url="https://uconn.edu/news/jane-doe-nature-paper",
        publication_date="2023-03-15",
    )

    # Document 4: Promotion announcement (contains similar info about NSF award)
    aggregator.add_document(
        entity_name="Professor Jane Doe",
        entity_type="person",
        content="""
        The University of Connecticut is pleased to announce the promotion of Dr. Jane Doe
        to Full Professor, effective July 1, 2024. Professor Doe's distinguished career
        includes receiving the NSF CAREER Award and publishing over 50 papers in top-tier
        conferences. She is recognized as a leader in explainable AI research.
        """,
        source_url="https://uconn.edu/news/jane-doe-promotion",
        publication_date="2024-06-01",
    )

    print("Documents added. Performing semantic deduplication...\n")

    # Deduplicate facts
    deduplicated_facts = aggregator.deduplicate_facts("Professor Jane Doe")

    print(f"Result: {len(aggregator.entity_facts['Professor Jane Doe'])} raw facts "
          f"→ {len(deduplicated_facts)} unique facts after deduplication\n")

    print("Deduplicated Facts:")
    print("-" * 80)
    for i, fact in enumerate(deduplicated_facts, 1):
        pub_date = fact["publication_date"].strftime("%Y-%m-%d") if fact["publication_date"] else "Unknown"
        source_count = len(fact.get("source_references", []))
        print(f"{i}. [{pub_date}] {fact['fact_text']}")
        print(f"   Sources: {source_count} document(s)")
        print()

    return deduplicated_facts


def demo_chronological_sorting(facts):
    """Demonstrate Phase 2a: Chronological Sorting."""
    print("\n" + "="*80)
    print("PHASE 2A: CHRONOLOGICAL SORTING WITH DATE CONTEXT")
    print("="*80 + "\n")

    sorter = ChronologicalSorter(date_format="%Y-%m-%d")

    # Prepare facts for summarization
    formatted_text = sorter.prepare_for_summarization(facts)

    print("Facts sorted chronologically with date prefixes:")
    print("-" * 80)
    print(formatted_text)
    print()

    return formatted_text


def demo_abstractive_summarization(formatted_text, facts):
    """Demonstrate Phase 2b: Abstractive Summarization with Citations."""
    print("\n" + "="*80)
    print("PHASE 2B: ABSTRACTIVE SUMMARIZATION WITH CITATIONS")
    print("="*80 + "\n")

    summarizer = AbstractiveSummarizer(
        model_name="facebook/bart-large-cnn",
        max_length=300,
        min_length=100,
        device=-1,  # CPU
    )

    print("Generating abstractive summary using BART...")
    print("(This may take a minute on first run while downloading the model)\n")

    # Generate summary
    result = summarizer.summarize(formatted_text, facts)

    print("Generated Summary:")
    print("-" * 80)
    print(result["summary_text"])
    print()

    print("Citations:")
    print("-" * 80)
    for citation_num, sources in result["citations"].items():
        print(f"[{citation_num}] {len(sources)} source(s):")
        for source in sources:
            print(f"    - {source['source_url']}")
            if source.get("publication_date"):
                print(f"      Date: {source['publication_date'].strftime('%Y-%m-%d')}")
        print()

    return result


def demo_full_pipeline():
    """Demonstrate the complete end-to-end pipeline using Stage4EntityWorker."""
    print("\n" + "="*80)
    print("FULL PIPELINE DEMO: END-TO-END ENTITY SUMMARIZATION")
    print("="*80 + "\n")

    # Initialize worker
    worker = Stage4EntityWorker(
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        summarization_model="facebook/bart-large-cnn",
        similarity_threshold=0.85,
        device=-1,
    )

    # Sample documents for multiple entities
    documents = [
        # Documents about Professor Jane Doe
        {
            "entity_name": "Professor Jane Doe",
            "entity_type": "person",
            "content": "Professor Jane Doe joined UConn in 2020. She leads the AI Research Lab.",
            "source_url": "https://uconn.edu/faculty/jane-doe",
            "publication_date": datetime(2020, 8, 15),
        },
        {
            "entity_name": "Professor Jane Doe",
            "entity_type": "person",
            "content": "Dr. Jane Doe received the NSF CAREER Award in 2021 for her work on neural networks.",
            "source_url": "https://uconn.edu/news/jane-doe-award",
            "publication_date": datetime(2021, 3, 10),
        },
        # Documents about UConn AI Lab
        {
            "entity_name": "UConn AI Research Lab",
            "entity_type": "organization",
            "content": "The UConn AI Research Lab was established in 2020 under the direction of Professor Jane Doe.",
            "source_url": "https://uconn.edu/research/ai-lab",
            "publication_date": datetime(2020, 9, 1),
        },
        {
            "entity_name": "UConn AI Research Lab",
            "entity_type": "organization",
            "content": "The AI Lab has published over 50 papers in top conferences. Current research areas include computer vision and NLP.",
            "source_url": "https://uconn.edu/research/ai-lab/publications",
            "publication_date": datetime(2024, 1, 15),
        },
    ]

    print(f"Processing {len(documents)} documents for {len(set(d['entity_name'] for d in documents))} entities...")
    print()

    # Process all documents
    worker.process_documents(documents)

    print("\n" + "="*80)
    print("PIPELINE COMPLETE!")
    print("="*80)
    print("\nEntity summaries have been stored in Delta Lake table: 'entity_summaries'")
    print("\nTo query the results, run:")
    print("  from src.common.delta_lake import get_delta_manager")
    print("  delta = get_delta_manager()")
    print("  summaries = delta.read('entity_summaries')")
    print()


def main():
    """Run all demos."""
    print("\n" + "="*80)
    print("ENTITY-CENTRIC SUMMARIZATION PIPELINE DEMONSTRATION")
    print("="*80)
    print("\nThis demo shows how the pipeline transforms raw documents into")
    print("verifiable, non-redundant, chronologically-aware entity summaries.\n")

    try:
        # Demo Phase 1: Fact Aggregation
        facts = demo_fact_aggregator()

        # Demo Phase 2a: Chronological Sorting
        formatted_text = demo_chronological_sorting(facts)

        # Demo Phase 2b: Abstractive Summarization
        summary_result = demo_abstractive_summarization(formatted_text, facts)

        # Demo Full Pipeline
        demo_full_pipeline()

        print("\n✅ Demo completed successfully!")
        print("\nNext steps:")
        print("  1. Review the generated summaries in Delta Lake")
        print("  2. Integrate with your existing pipeline (see entity_worker_example.py)")
        print("  3. Customize entity extraction logic for your domain")
        print("  4. Tune similarity_threshold for optimal deduplication")
        print()

    except ImportError as e:
        print(f"\n❌ Error: Missing required package: {e}")
        print("\nPlease install dependencies:")
        print("  pip install -r requirements-stage4.txt")
        print()
        return 1

    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
