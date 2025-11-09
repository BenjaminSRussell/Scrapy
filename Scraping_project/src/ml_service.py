import json
import logging
import os
from typing import Any

try:
    from confluent_kafka import Consumer, KafkaError, Producer

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    Consumer = None
    Producer = None
    KafkaError = None

try:
    from transformers import pipeline

    TRANSFORMERS_AVAILABLE = True
except ImportError:
    TRANSFORMERS_AVAILABLE = False
    pipeline = None

from src.schemas import CategoryType, LowConfidenceRecord

logger = logging.getLogger(__name__)

class ZeroShotClassifier:

    HYPOTHESIS_TEMPLATES = [
        "The specific domain of this text is {}.",
        "This content is primarily about {}.",
        "The main topic category is {}.",
    ]

    def __init__(
        self,
        model_name: str = "facebook/bart-large-mnli",
        confidence_threshold: float = 0.85,
        device: int = -1,
    ):
        """Initialize the Zero-Shot Classifier.

        Args:
            model_name: Pre-trained NLI model name (HuggingFace)
            confidence_threshold: Minimum confidence score [0.0, 1.0]
            device: Device for inference (-1=CPU, 0=GPU:0, etc.)

        Raises:
            ImportError: If transformers library is not available
        """
        if not TRANSFORMERS_AVAILABLE:
            raise ImportError(
                "transformers library required for ZeroShotClassifier. Install with: pip install transformers torch"
            )

        self.model_name = model_name
        self.confidence_threshold = confidence_threshold
        self.device = device

        logger.info(f"Loading zero-shot model: {model_name}")
        self.classifier = pipeline(
            "zero-shot-classification",
            model=model_name,
            device=device,
        )
        logger.info("Zero-shot model loaded successfully")

        self.candidate_labels = [
            "tuition and fees",
            "housing costs",
            "faculty research",
            "student life",
            "academic programs",
            "financial aid",
            "admissions",
            "campus facilities",
            "other",
        ]

    def classify(self, text: str, multi_label: bool = False) -> dict[str, Any]:
        if not text or not text.strip():
            logger.warning("Empty text provided for classification")
            return {
                "category": CategoryType.OTHER,
                "confidence": 0.0,
                "all_scores": {},
                "meets_threshold": False,
            }

        result = self.classifier(
            text,
            self.candidate_labels,
            hypothesis_template=self.HYPOTHESIS_TEMPLATES[0],
            multi_label=multi_label,
        )

        top_label = result["labels"][0]
        top_score = result["scores"][0]

        category_map = {
            "tuition and fees": CategoryType.TUITION_FEES,
            "housing costs": CategoryType.HOUSING_COSTS,
            "faculty research": CategoryType.FACULTY_RESEARCH,
            "student life": CategoryType.STUDENT_LIFE,
            "academic programs": CategoryType.ACADEMIC_PROGRAMS,
            "financial aid": CategoryType.FINANCIAL_AID,
            "admissions": CategoryType.ADMISSIONS,
            "campus facilities": CategoryType.CAMPUS_FACILITIES,
            "other": CategoryType.OTHER,
        }

        predicted_category = category_map.get(top_label, CategoryType.OTHER)

        all_scores = dict(zip(result["labels"], result["scores"], strict=False))

        meets_threshold = top_score >= self.confidence_threshold

        return {
            "category": predicted_category,
            "confidence": float(top_score),
            "all_scores": all_scores,
            "meets_threshold": meets_threshold,
        }

    def classify_batch(self, texts: list[str], multi_label: bool = False) -> list[dict[str, Any]]:
        return [self.classify(text, multi_label=multi_label) for text in texts]

class ZSCMicroservice:

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        input_topic: str = "validated_items",
        output_topic: str = "final_categorized",
        low_confidence_topic: str = "low_confidence_review",
        group_id: str = "zsc-service",
        confidence_threshold: float = 0.85,
        model_name: str = "facebook/bart-large-mnli",
        device: int = -1,
    ):
        """Initialize the ZSC microservice.

        Args:
            bootstrap_servers: Kafka broker addresses
            input_topic: Topic to consume validated items from
            output_topic: Topic for high-confidence classifications
            low_confidence_topic: Topic for low-confidence items
            group_id: Kafka consumer group ID
            confidence_threshold: Minimum confidence threshold
            model_name: Pre-trained NLI model name
            device: Device for inference (-1=CPU, 0=GPU)
        """
        if not KAFKA_AVAILABLE:
            raise ImportError("confluent_kafka required for ZSCMicroservice")

        self.bootstrap_servers = bootstrap_servers
        self.input_topic = input_topic
        self.output_topic = output_topic
        self.low_confidence_topic = low_confidence_topic
        self.group_id = group_id

        self.classifier = ZeroShotClassifier(
            model_name=model_name,
            confidence_threshold=confidence_threshold,
            device=device,
        )

        self.consumer: Any = None
        self.producer: Any = None

        self.items_processed = 0
        self.items_high_confidence = 0
        self.items_low_confidence = 0

    def start(self):
        logger.info("Starting ZSC Microservice")

        consumer_config = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }

        self._add_security_config(consumer_config)

        self.consumer = Consumer(consumer_config)
        self.consumer.subscribe([self.input_topic])

        producer_config = {
            "bootstrap.servers": self.bootstrap_servers,
            "linger.ms": 10,
            "compression.type": "snappy",
            "acks": 1,
        }

        self._add_security_config(producer_config)

        self.producer = Producer(producer_config)

        logger.info(f"Consuming from topic: {self.input_topic}")
        logger.info(f"Publishing to: {self.output_topic}, {self.low_confidence_topic}")

        try:
            while True:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        logger.error(f"Consumer error: {msg.error()}")
                        break

                self._process_message(msg)

        except KeyboardInterrupt:
            logger.info("Shutdown signal received")
        finally:
            self._shutdown()

    def _process_message(self, msg: Any):
        try:
            item_dict = json.loads(msg.value().decode("utf-8"))

            text = self._extract_text(item_dict)

            if not text:
                logger.warning(f"No text found in item: {item_dict.get('url')}")
                return

            classification = self.classifier.classify(text)

            item_dict["category_final"] = classification["category"].value
            item_dict["category_confidence"] = classification["confidence"]

            self.items_processed += 1

            if classification["meets_threshold"]:
                self._publish_item(self.output_topic, item_dict)
                self.items_high_confidence += 1
            else:
                low_conf_record = LowConfidenceRecord(
                    url=item_dict.get("url", ""),
                    title=item_dict.get("title", ""),
                    content_preview=text[:500],
                    predicted_category=classification["category"],
                    confidence_score=classification["confidence"],
                    threshold=self.classifier.confidence_threshold,
                )
                self._publish_low_confidence(low_conf_record)
                self.items_low_confidence += 1

            if self.items_processed % 100 == 0:
                logger.info(
                    f"ZSC Progress: {self.items_processed} processed, "
                    f"{self.items_high_confidence} high-conf, "
                    f"{self.items_low_confidence} low-conf"
                )

        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def _extract_text(self, item_dict: dict[str, Any]) -> str:
        parts = []

        title = item_dict.get("title", "")
        if title:
            parts.append(title)

        content = item_dict.get("content", "")
        if content:
            parts.append(content[:1000])

        return " ".join(parts)

    def _publish_item(self, topic: str, item_dict: dict[str, Any]):
        try:
            message = json.dumps(item_dict, ensure_ascii=False, default=str)
            self.producer.produce(
                topic=topic,
                value=message.encode("utf-8"),
            )
            self.producer.poll(0)
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")

    def _publish_low_confidence(self, record: LowConfidenceRecord):
        try:
            message = record.model_dump_json()
            self.producer.produce(
                topic=self.low_confidence_topic,
                value=message.encode("utf-8"),
            )
            self.producer.poll(0)
        except Exception as e:
            logger.error(f"Failed to publish low-confidence record: {e}")

    def _add_security_config(self, config: dict[str, Any]):
        security_protocol = os.getenv("KAFKA_SECURITY_PROTOCOL")
        if security_protocol:
            config["security.protocol"] = security_protocol

        sasl_mechanism = os.getenv("KAFKA_SASL_MECHANISM")
        if sasl_mechanism:
            config["sasl.mechanism"] = sasl_mechanism

        sasl_username = os.getenv("KAFKA_SASL_USERNAME")
        if sasl_username:
            config["sasl.username"] = sasl_username

        sasl_password = os.getenv("KAFKA_SASL_PASSWORD")
        if sasl_password:
            config["sasl.password"] = sasl_password

    def _shutdown(self):
        logger.info("Shutting down ZSC Microservice")

        if self.producer:
            remaining = self.producer.flush(timeout=30.0)
            if remaining > 0:
                logger.warning(f"{remaining} messages not delivered")

        if self.consumer:
            self.consumer.close()

        logger.info(
            f"Final stats - Processed: {self.items_processed}, "
            f"High-conf: {self.items_high_confidence}, "
            f"Low-conf: {self.items_low_confidence}"
        )

def main():
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    input_topic = os.getenv("ZSC_INPUT_TOPIC", "validated_items")
    output_topic = os.getenv("ZSC_OUTPUT_TOPIC", "final_categorized")
    low_confidence_topic = os.getenv("ZSC_LOW_CONF_TOPIC", "low_confidence_review")
    confidence_threshold = float(os.getenv("ZSC_CONFIDENCE_THRESHOLD", "0.85"))
    model_name = os.getenv("ZSC_MODEL_NAME", "facebook/bart-large-mnli")
    device = int(os.getenv("ZSC_DEVICE", "-1"))

    service = ZSCMicroservice(
        bootstrap_servers=bootstrap_servers,
        input_topic=input_topic,
        output_topic=output_topic,
        low_confidence_topic=low_confidence_topic,
        confidence_threshold=confidence_threshold,
        model_name=model_name,
        device=device,
    )

    try:
        service.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
