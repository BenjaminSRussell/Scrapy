"""
Vendor Data Integration Framework

Provides framework for integrating third-party data sources that aren't accessible
through web crawling. This broadens data collection beyond what's linked from the
main university website.

Supported vendor types:
- API integrations (REST, GraphQL)
- Manual data imports (CSV, JSON, Excel)
- Database extracts (MySQL, PostgreSQL, MongoDB)
- Document repositories (SharePoint, Google Drive)
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from src.common.warehouse import DataWarehouse
from src.common.warehouse_schema import VendorDataRecord

logger = logging.getLogger(__name__)


@dataclass
class VendorConfig:
    """Vendor data source configuration"""
    name: str
    type: str  # 'api', 'file', 'database', 'manual'
    url: str | None = None
    credentials: dict[str, str] | None = None
    mapping: dict[str, str] | None = None  # Field mapping to warehouse schema
    enabled: bool = True


class VendorDataSource(ABC):
    """Abstract base class for vendor data sources"""

    def __init__(self, config: VendorConfig):
        self.config = config
        self.warehouse = None

    def set_warehouse(self, warehouse: DataWarehouse):
        """Set warehouse connection"""
        self.warehouse = warehouse

    @abstractmethod
    def extract_data(self) -> list[dict[str, Any]]:
        """Extract data from vendor source"""
        pass

    def load_to_warehouse(self, data: list[dict[str, Any]]) -> int:
        """Load extracted data to warehouse"""
        if not self.warehouse:
            raise RuntimeError("Warehouse not configured")

        loaded_count = 0
        for record in data:
            vendor_record = VendorDataRecord(
                vendor_name=self.config.name,
                vendor_url=self.config.url,
                data_type=self.config.type,
                raw_data=record,
                extracted_at=datetime.now()
            )

            self.warehouse.insert_vendor_data(vendor_record)
            loaded_count += 1

        logger.info(f"Loaded {loaded_count} records from vendor {self.config.name}")
        return loaded_count


class APIVendorSource(VendorDataSource):
    """Vendor source that pulls data from REST API"""

    def extract_data(self) -> list[dict[str, Any]]:
        """Extract data from API endpoint"""
        if not self.config.url:
            raise ValueError(f"API URL not configured for {self.config.name}")

        headers = {}
        if self.config.credentials:
            api_key = self.config.credentials.get('api_key')
            if api_key:
                headers['Authorization'] = f"Bearer {api_key}"

        try:
            response = requests.get(self.config.url, headers=headers, timeout=30)
            response.raise_for_status()

            data = response.json()

            # Handle different API response structures
            if isinstance(data, dict):
                # Check for common data wrapper keys
                if 'data' in data:
                    return data['data'] if isinstance(data['data'], list) else [data['data']]
                if 'results' in data:
                    return data['results'] if isinstance(data['results'], list) else [data['results']]
                if 'items' in data:
                    return data['items'] if isinstance(data['items'], list) else [data['items']]
                return [data]

            return data if isinstance(data, list) else [data]

        except requests.RequestException as e:
            logger.error(f"Failed to extract data from API {self.config.url}: {e}")
            return []


class FileVendorSource(VendorDataSource):
    """Vendor source that reads from file (JSON, CSV, Excel)"""

    def extract_data(self) -> list[dict[str, Any]]:
        """Extract data from file"""
        if not self.config.url:
            raise ValueError(f"File path not configured for {self.config.name}")

        file_path = Path(self.config.url)

        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return []

        try:
            if file_path.suffix == '.json':
                return self._extract_json(file_path)
            elif file_path.suffix == '.jsonl':
                return self._extract_jsonl(file_path)
            elif file_path.suffix == '.csv':
                return self._extract_csv(file_path)
            elif file_path.suffix in ['.xlsx', '.xls']:
                return self._extract_excel(file_path)
            else:
                logger.error(f"Unsupported file type: {file_path.suffix}")
                return []

        except Exception as e:
            logger.error(f"Failed to extract data from file {file_path}: {e}")
            return []

    def _extract_json(self, file_path: Path) -> list[dict[str, Any]]:
        """Extract from JSON file"""
        with open(file_path, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else [data]

    def _extract_jsonl(self, file_path: Path) -> list[dict[str, Any]]:
        """Extract from JSONL file"""
        data = []
        with open(file_path, encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data.append(json.loads(line))
        return data

    def _extract_csv(self, file_path: Path) -> list[dict[str, Any]]:
        """Extract from CSV file"""
        import csv

        data = []
        with open(file_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                data.append(dict(row))
        return data

    def _extract_excel(self, file_path: Path) -> list[dict[str, Any]]:
        """Extract from Excel file"""
        try:
            import pandas as pd

            df = pd.read_excel(file_path)
            return df.to_dict('records')
        except ImportError:
            logger.error("pandas and openpyxl required for Excel support: pip install pandas openpyxl")
            return []


class DatabaseVendorSource(VendorDataSource):
    """Vendor source that queries external database"""

    def extract_data(self) -> list[dict[str, Any]]:
        """Extract data from database"""
        if not self.config.url:
            raise ValueError(f"Database connection string not configured for {self.config.name}")

        db_type = self.config.credentials.get('db_type', 'mysql') if self.config.credentials else 'mysql'
        query = self.config.credentials.get('query', 'SELECT * FROM data_table LIMIT 1000') if self.config.credentials else 'SELECT * FROM data_table LIMIT 1000'

        try:
            if db_type == 'mysql':
                return self._extract_mysql(query)
            elif db_type == 'postgresql':
                return self._extract_postgresql(query)
            elif db_type == 'mongodb':
                return self._extract_mongodb()
            else:
                logger.error(f"Unsupported database type: {db_type}")
                return []

        except Exception as e:
            logger.error(f"Failed to extract data from database: {e}")
            return []

    def _extract_mysql(self, query: str) -> list[dict[str, Any]]:
        """Extract from MySQL database"""
        try:
            import mysql.connector

            conn = mysql.connector.connect(
                host=self.config.credentials.get('host', 'localhost'),
                user=self.config.credentials.get('user', ''),
                password=self.config.credentials.get('password', ''),
                database=self.config.credentials.get('database', '')
            )

            cursor = conn.cursor(dictionary=True)
            cursor.execute(query)
            data = cursor.fetchall()

            cursor.close()
            conn.close()

            return data

        except ImportError:
            logger.error("mysql-connector-python required: pip install mysql-connector-python")
            return []

    def _extract_postgresql(self, query: str) -> list[dict[str, Any]]:
        """Extract from PostgreSQL database"""
        try:
            import psycopg2
            import psycopg2.extras

            conn = psycopg2.connect(self.config.url)
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

            cursor.execute(query)
            data = cursor.fetchall()

            cursor.close()
            conn.close()

            return [dict(row) for row in data]

        except ImportError:
            logger.error("psycopg2 required: pip install psycopg2-binary")
            return []

    def _extract_mongodb(self) -> list[dict[str, Any]]:
        """Extract from MongoDB"""
        try:
            from pymongo import MongoClient

            client = MongoClient(self.config.url)
            db_name = self.config.credentials.get('database', 'test')
            collection_name = self.config.credentials.get('collection', 'data')

            db = client[db_name]
            collection = db[collection_name]

            limit = self.config.credentials.get('limit', 1000)
            data = list(collection.find().limit(limit))

            # Convert ObjectId to string
            for doc in data:
                if '_id' in doc:
                    doc['_id'] = str(doc['_id'])

            client.close()
            return data

        except ImportError:
            logger.error("pymongo required: pip install pymongo")
            return []


class VendorIntegrationManager:
    """Manages multiple vendor data sources"""

    def __init__(self, warehouse: DataWarehouse):
        self.warehouse = warehouse
        self.vendors: dict[str, VendorDataSource] = {}

    def register_vendor(self, config: VendorConfig) -> None:
        """Register a new vendor data source"""
        if config.type == 'api':
            vendor = APIVendorSource(config)
        elif config.type in ['file', 'manual']:
            vendor = FileVendorSource(config)
        elif config.type == 'database':
            vendor = DatabaseVendorSource(config)
        else:
            raise ValueError(f"Unknown vendor type: {config.type}")

        vendor.set_warehouse(self.warehouse)
        self.vendors[config.name] = vendor

        logger.info(f"Registered vendor: {config.name} ({config.type})")

    def load_vendor_config(self, config_path: str | Path) -> None:
        """Load vendor configurations from JSON file"""
        config_path = Path(config_path)

        if not config_path.exists():
            logger.warning(f"Vendor config file not found: {config_path}")
            return

        with open(config_path, encoding='utf-8') as f:
            config_data = json.load(f)

        for vendor_config in config_data.get('vendors', []):
            config = VendorConfig(**vendor_config)
            if config.enabled:
                self.register_vendor(config)

    def extract_all(self) -> dict[str, int]:
        """Extract data from all vendors and load to warehouse"""
        results = {}

        for name, vendor in self.vendors.items():
            logger.info(f"Extracting data from vendor: {name}")

            try:
                data = vendor.extract_data()
                count = vendor.load_to_warehouse(data)
                results[name] = count
            except Exception as e:
                logger.error(f"Failed to process vendor {name}: {e}", exc_info=True)
                results[name] = 0

        return results


# Example vendor configuration file structure
VENDOR_CONFIG_EXAMPLE = """
{
  "vendors": [
    {
      "name": "UConn People Directory API",
      "type": "api",
      "url": "https://api.uconn.edu/people",
      "credentials": {
        "api_key": "your-api-key-here"
      },
      "enabled": true
    },
    {
      "name": "Course Catalog Extract",
      "type": "file",
      "url": "data/vendor/course_catalog.json",
      "enabled": true
    },
    {
      "name": "HR Employee Database",
      "type": "database",
      "url": "postgresql://localhost/hr_db",
      "credentials": {
        "db_type": "postgresql",
        "query": "SELECT * FROM employees WHERE department = 'Academic Affairs'"
      },
      "enabled": false
    }
  ]
}
"""
