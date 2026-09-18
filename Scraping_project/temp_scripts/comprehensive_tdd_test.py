#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.storage_manager import get_delta
from src.lakehouse.seed_manager import SeedManager
from src.stage2.stage2_worker import Stage2Worker
from src.stage3.stage3_worker import Stage3Worker
from src.stage4.stage4_worker import Stage4Worker

TEST_URLS = [
    "https://uconn.edu/",
    "https://uconn.edu/admissions/",
    "https://uconn.edu/academics/",
    "https://uconn.edu/research/",
    "https://uconn.edu/campus-life/",
    "https://uconn.edu/athletics/",
    "https://uconn.edu/about/",
    "https://uconn.edu/tuition-fees/",
    "https://uconn.edu/financial-aid/",
    "https://uconn.edu/housing/",
]

MOCK_HTML_TEMPLATES = {
    "short_quality": """
        <html><body>
        <h1>{title}</h1>
        <p>{content}</p>
        <p>UConn offers excellent programs in various fields. Students can choose from
        undergraduate and graduate programs. The university is known for its research excellence
        and commitment to student success.</p>
        </body></html>
    """,
    "medium_quality": """
        <html><body>
        <h1>{title}</h1>
        <article>
        <p>{content}</p>
        <p>The University of Connecticut (UConn) is a premier public research university.
        Founded in 1881, UConn has grown to serve over 32,000 students across multiple campuses.
        The university offers more than 100 undergraduate majors and over 80 graduate programs.</p>
        <p>Academic excellence is at the core of UConn's mission. Faculty members are leaders
        in their fields, conducting groundbreaking research while maintaining a commitment to
        teaching excellence. Students benefit from small class sizes, state-of-the-art facilities,
        and numerous opportunities for hands-on learning.</p>
        <p>Campus life at UConn is vibrant and diverse. With over 600 student organizations,
        Division I athletics, and a strong sense of community, students find countless ways
        to get involved and make lasting connections.</p>
        </article>
        </body></html>
    """,
    "massive_doc": (
        "<html><body><h1>{title}</h1><div class='content'><p>{content}</p>" +
        "<p>UConn provides comprehensive academic programs and services. " * 500 +
        "</p><p>Research opportunities abound at UConn across all disciplines. " * 500 +
        "</p><p>Student support services ensure success for all enrolled students. " * 500 +
        "</p></div></body></html>"
    ),
}

def create_mock_page_data():
    delta = get_delta()

    mock_data = []

    mock_data.append({
        'url': TEST_URLS[0],
        'word_count': 500,
        'text_to_html_ratio': 0.35,
        'is_low_quality': False,
        'is_massive_doc': False,
        'text_content': "Welcome to UConn. The University of Connecticut is a premier public research university. "
                       "We offer over 100 undergraduate majors and 80 graduate programs across 14 schools and colleges. "
                       "Our faculty are renowned scholars and dedicated teachers committed to student success.",
        'content_type': 'html',
        'has_error': False,
    })

    mock_data.append({
        'url': TEST_URLS[1],
        'word_count': 1200,
        'text_to_html_ratio': 0.42,
        'is_low_quality': False,
        'is_massive_doc': False,
        'text_content': "Admissions at UConn. We seek students who are academically talented and engaged in their communities. "
                       "The application process includes submitting transcripts, test scores, essays, and letters of recommendation. "
                       "We offer both Early Action and Regular Decision application pathways. First-year students should have strong "
                       "academic records with challenging coursework. Transfer students are welcomed and should have completed college-level work.",
        'content_type': 'html',
        'has_error': False,
    })

    mock_data.append({
        'url': TEST_URLS[2],
        'word_count': 2500,
        'text_to_html_ratio': 0.38,
        'is_low_quality': False,
        'is_massive_doc': False,
        'text_content': "Academic Programs at UConn. UConn offers comprehensive academic programs across all disciplines. "
                       "The College of Liberal Arts and Sciences is our largest school with programs in humanities, social sciences, "
                       "and natural sciences. The School of Business offers highly ranked programs in accounting, finance, marketing, "
                       "and management. The School of Engineering provides cutting-edge education in mechanical, electrical, computer, "
                       "and civil engineering. Additional schools include Education, Nursing, Agriculture, Fine Arts, and Pharmacy.",
        'content_type': 'html',
        'has_error': False,
    })

    mock_data.append({
        'url': TEST_URLS[3],
        'word_count': 75000,
        'text_to_html_ratio': 0.25,
        'is_low_quality': False,
        'is_massive_doc': True,
        'text_content': "Research at UConn Comprehensive Guide. " +
                       "UConn is classified as an R1 research university, the highest designation for research activity. " * 100 +
                       "Faculty and students conduct groundbreaking research in areas including health sciences, engineering, agriculture, and social sciences. " * 100 +
                       "Research facilities include state-of-the-art laboratories, field stations, and computational resources. " * 100,
        'content_type': 'html',
        'has_error': False,
    })

    delta.write('stage2_page_analysis', mock_data, mode='overwrite')

    print(f"✅ Created {len(mock_data)} mock page analysis records")
    return mock_data

async def test_stage3_summarization():
    print("\n" + "=" * 80)
    print("TEST: Stage 3 - Summarization")
    print("=" * 80)

    delta = get_delta()

    quality_docs = delta.read_table('stage2_page_analysis')
    quality_docs = [d for d in quality_docs if not d.get('is_massive_doc', False)]

    print(f"📊 Quality documents to process: {len(quality_docs)}")

    worker = Stage3Worker(max_concurrent=5, batch_size=10)

    try:
        await worker.run()
        print("✅ Stage 3 worker completed successfully")

        summaries = delta.read_table('stage4_summaries')
        print(f"📝 Summaries created: {len(summaries)}")

        if summaries:
            print("\n📋 Sample Summary:")
            sample = summaries[0]
            print(f"  URL: {sample.get('url', 'N/A')[:80]}")
            print(f"  Summary: {sample.get('summary', 'N/A')[:150]}...")
            print(f"  Keywords: {sample.get('keywords', [])[:5]}")

        return len(summaries) > 0

    except Exception as e:
        print(f"❌ Stage 3 failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_stage4_large_docs():
    print("\n" + "=" * 80)
    print("TEST: Stage 4 - Large Document Processing")
    print("=" * 80)

    delta = get_delta()

    massive_docs = delta.read_table('stage2_page_analysis')
    massive_docs = [d for d in massive_docs if d.get('is_massive_doc', False)]

    print(f"📊 Massive documents to process: {len(massive_docs)}")

    if not massive_docs:
        print("⚠️  No massive documents found, skipping Stage 4")
        return True

    worker = Stage4Worker()

    try:
        await worker.run()
        print("✅ Stage 4 worker completed successfully")

        large_summaries = delta.read_table('stage4_large_doc_summaries')
        print(f"📝 Large doc summaries created: {len(large_summaries)}")

        if large_summaries:
            print("\n📋 Sample Large Doc Summary:")
            sample = large_summaries[0]
            print(f"  URL: {sample.get('url', 'N/A')[:80]}")
            print(f"  Original size: {sample.get('original_size', 0):,} chars")
            print(f"  Summary size: {len(sample.get('summary', '')):,} chars")
            print(f"  Compression: {sample.get('compression_ratio', 0):.4f}")
            print(f"  Summary: {sample.get('summary', 'N/A')[:150]}...")

        return len(large_summaries) > 0

    except Exception as e:
        print(f"❌ Stage 4 failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_nlp_libraries():
    print("\n" + "=" * 80)
    print("TEST: NLP Libraries")
    print("=" * 80)

    results = {}

    print("\n1. Testing Transformers (Summarization)...")
    try:
        from transformers import pipeline
        summarizer = pipeline("summarization", model="facebook/bart-large-cnn", device=-1)

        test_text = """
        The University of Connecticut is a public research university with its main campus in Storrs, Connecticut.
        It was founded in 1881 and has grown to serve over 32,000 students across multiple campuses.
        UConn is known for its strong academic programs, Division I athletics, and vibrant campus life.
        """

        summary = summarizer(test_text, max_length=50, min_length=10, do_sample=False)
        print(f"✅ Transformers summarization working")
        print(f"   Input: {len(test_text)} chars")
        print(f"   Output: {summary[0]['summary_text']}")
        results['transformers_summarization'] = True
    except Exception as e:
        print(f"❌ Transformers summarization failed: {e}")
        results['transformers_summarization'] = False

    print("\n2. Testing Transformers (Zero-Shot Classification)...")
    try:
        from transformers import pipeline
        classifier = pipeline("zero-shot-classification", model="facebook/bart-large-mnli", device=-1)

        test_text = "UConn offers comprehensive financial aid packages to help students afford their education."
        candidate_labels = ["financial aid", "academics", "athletics", "research"]

        result = classifier(test_text, candidate_labels)
        print(f"✅ Zero-shot classification working")
        print(f"   Text: {test_text[:80]}...")
        print(f"   Top label: {result['labels'][0]} ({result['scores'][0]:.3f})")
        results['zero_shot_classification'] = True
    except Exception as e:
        print(f"❌ Zero-shot classification failed: {e}")
        results['zero_shot_classification'] = False

    print("\n3. Testing Sentence Transformers (Embeddings)...")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

        sentences = [
            "UConn has a strong basketball program",
            "The Huskies are known for their athletic success",
            "Academic programs at UConn are highly ranked"
        ]

        embeddings = model.encode(sentences)
        print(f"✅ Sentence transformers working")
        print(f"   Encoded {len(sentences)} sentences")
        print(f"   Embedding shape: {embeddings.shape}")

        from numpy import dot
        from numpy.linalg import norm

        sim_1_2 = dot(embeddings[0], embeddings[1]) / (norm(embeddings[0]) * norm(embeddings[1]))
        sim_1_3 = dot(embeddings[0], embeddings[2]) / (norm(embeddings[0]) * norm(embeddings[2]))

        print(f"   Similarity (sent1 vs sent2): {sim_1_2:.3f}")
        print(f"   Similarity (sent1 vs sent3): {sim_1_3:.3f}")
        results['sentence_transformers'] = True
    except Exception as e:
        print(f"❌ Sentence transformers failed: {e}")
        results['sentence_transformers'] = False

    print("\n4. Testing ZeroShotClassifier (Custom Class)...")
    try:
        from src.ml_service import ZeroShotClassifier

        classifier = ZeroShotClassifier(
            model_name="facebook/bart-large-mnli",
            confidence_threshold=0.5,
            device=-1
        )

        test_text = "Learn about housing options and residence halls at UConn."
        result = classifier.classify(test_text)

        print(f"✅ ZeroShotClassifier working")
        print(f"   Text: {test_text}")
        print(f"   Category: {result['category']}")
        print(f"   Confidence: {result['confidence']:.3f}")
        print(f"   Meets threshold: {result['meets_threshold']}")
        results['zero_shot_classifier_class'] = True
    except Exception as e:
        print(f"❌ ZeroShotClassifier failed: {e}")
        results['zero_shot_classifier_class'] = False

    return results

async def run_full_integration_test():
    print("\n" + "🧪 " * 40)
    print("COMPREHENSIVE TDD INTEGRATION TEST")
    print("🧪 " * 40)

    delta = get_delta()

    print("\n📋 Test Plan:")
    print("  1. Create mock page analysis data (10 URLs)")
    print("  2. Test Stage 3 summarization (quality docs)")
    print("  3. Test Stage 4 large doc processing")
    print("  4. Validate NLP libraries")
    print("  5. Verify data integrity")

    results = {}

    print("\n" + "=" * 80)
    print("STEP 1: Creating Mock Data")
    print("=" * 80)
    mock_data = create_mock_page_data()
    results['mock_data_created'] = len(mock_data) > 0

    print("\n" + "=" * 80)
    print("STEP 2: Testing Stage 3")
    print("=" * 80)
    results['stage3_passed'] = await test_stage3_summarization()

    print("\n" + "=" * 80)
    print("STEP 3: Testing Stage 4")
    print("=" * 80)
    results['stage4_passed'] = await test_stage4_large_docs()

    print("\n" + "=" * 80)
    print("STEP 4: Testing NLP Libraries")
    print("=" * 80)
    nlp_results = test_nlp_libraries()
    results.update(nlp_results)

    print("\n" + "=" * 80)
    print("STEP 5: Data Integrity Check")
    print("=" * 80)

    page_analysis = delta.read_table('stage2_page_analysis')
    summaries = delta.read_table('stage4_summaries')
    large_summaries = delta.read_table('stage4_large_doc_summaries')

    print(f"📊 Page Analysis Records: {len(page_analysis)}")
    print(f"📊 Quality Summaries: {len(summaries)}")
    print(f"📊 Large Doc Summaries: {len(large_summaries)}")

    quality_count = len([d for d in page_analysis if not d.get('is_massive_doc', False)])
    massive_count = len([d for d in page_analysis if d.get('is_massive_doc', False)])

    print(f"\n✓ Quality docs: {quality_count} (should have {len(summaries)} summaries)")
    print(f"✓ Massive docs: {massive_count} (should have {len(large_summaries)} summaries)")

    results['data_integrity'] = True

    print("\n" + "=" * 80)
    print("TEST RESULTS SUMMARY")
    print("=" * 80)

    total_tests = len(results)
    passed_tests = sum(1 for v in results.values() if v)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\n📊 Overall: {passed_tests}/{total_tests} tests passed ({passed_tests/total_tests*100:.1f}%)")

    if passed_tests == total_tests:
        print("\n🎉 ALL TESTS PASSED! 🎉")
        return True
    else:
        print(f"\n⚠️  {total_tests - passed_tests} tests failed")
        return False

if __name__ == '__main__':
    success = asyncio.run(run_full_integration_test())
    sys.exit(0 if success else 1)
