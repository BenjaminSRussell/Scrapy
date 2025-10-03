package edu.uconn.warehouse;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.batch.core.configuration.annotation.EnableBatchProcessing;

/**
 * Main application class for the UConn Warehouse ETL Loader.
 *
 * This application reads JSONL files produced by the Python scraping pipeline
 * and loads them into a PostgreSQL data warehouse with proper normalization
 * and change tracking.
 */
@SpringBootApplication
@EnableBatchProcessing
public class WarehouseLoaderApplication {

    public static void main(String[] args) {
        System.exit(SpringApplication.exit(
            SpringApplication.run(WarehouseLoaderApplication.class, args)
        ));
    }
}
