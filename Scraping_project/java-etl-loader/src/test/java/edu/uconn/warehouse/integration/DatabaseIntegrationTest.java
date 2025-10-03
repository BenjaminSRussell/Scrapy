package edu.uconn.warehouse.integration;

import edu.uconn.warehouse.entity.Category;
import edu.uconn.warehouse.entity.Entity;
import edu.uconn.warehouse.entity.Keyword;
import edu.uconn.warehouse.entity.Page;
import edu.uconn.warehouse.repository.PageRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests using Testcontainers with a real PostgreSQL database.
 * These tests verify that the application works correctly with the actual database.
 */
@SpringBootTest
@Testcontainers
@DisplayName("PostgreSQL Integration Tests with Testcontainers")
class DatabaseIntegrationTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:15-alpine")
        .withDatabaseName("testdb")
        .withUsername("test")
        .withPassword("test");

    @DynamicPropertySource
    static void configureProperties(DynamicPropertyRegistry registry) {
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
    }

    @Autowired
    private PageRepository pageRepository;

    @Test
    @DisplayName("Should persist page with all relationships to PostgreSQL")
    void shouldPersistPageWithRelationships() {
        // Given
        Page page = Page.builder()
            .url("https://uconn.edu/admissions")
            .urlHash("admissions123")
            .title("UConn Admissions")
            .textContent("Information about undergraduate admissions at UConn")
            .wordCount(7)
            .firstSeenAt(LocalDateTime.now())
            .lastCrawledAt(LocalDateTime.now())
            .crawlVersion(1)
            .isCurrent(true)
            .build();

        // Add entities
        Entity entity1 = Entity.builder()
            .entityText("University of Connecticut")
            .entityType("ORG")
            .confidence(new BigDecimal("0.95"))
            .build();
        page.addEntity(entity1);

        Entity entity2 = Entity.builder()
            .entityText("Storrs")
            .entityType("LOC")
            .confidence(new BigDecimal("0.88"))
            .build();
        page.addEntity(entity2);

        // Add keywords
        Keyword keyword1 = Keyword.builder()
            .keyword("admissions")
            .score(new BigDecimal("0.8542"))
            .build();
        page.addKeyword(keyword1);

        Keyword keyword2 = Keyword.builder()
            .keyword("undergraduate")
            .score(new BigDecimal("0.7231"))
            .build();
        page.addKeyword(keyword2);

        // Add categories
        Category category1 = Category.builder()
            .categoryName("Undergraduate Programs")
            .categoryPath("academics.undergraduate")
            .confidence(new BigDecimal("0.92"))
            .build();
        page.addCategory(category1);

        // When
        Page saved = pageRepository.save(page);
        pageRepository.flush();

        // Then
        assertThat(saved.getId()).isNotNull();

        // Verify page was saved
        Optional<Page> found = pageRepository.findById(saved.getId());
        assertThat(found).isPresent();

        Page loadedPage = found.get();
        assertThat(loadedPage.getUrl()).isEqualTo("https://uconn.edu/admissions");
        assertThat(loadedPage.getUrlHash()).isEqualTo("admissions123");
        assertThat(loadedPage.getTitle()).isEqualTo("UConn Admissions");
        assertThat(loadedPage.getWordCount()).isEqualTo(7);
        assertThat(loadedPage.getIsCurrent()).isTrue();

        // Verify entities
        assertThat(loadedPage.getEntities()).hasSize(2);
        assertThat(loadedPage.getEntities())
            .extracting(Entity::getEntityText)
            .containsExactlyInAnyOrder("University of Connecticut", "Storrs");

        // Verify keywords
        assertThat(loadedPage.getKeywords()).hasSize(2);
        assertThat(loadedPage.getKeywords())
            .extracting(Keyword::getKeyword)
            .containsExactlyInAnyOrder("admissions", "undergraduate");

        // Verify categories
        assertThat(loadedPage.getCategories()).hasSize(1);
        assertThat(loadedPage.getCategories().get(0).getCategoryName())
            .isEqualTo("Undergraduate Programs");
    }

    @Test
    @DisplayName("Should handle page versioning correctly in PostgreSQL")
    void shouldHandlePageVersioning() {
        // Given - Create version 1
        Page v1 = Page.builder()
            .url("https://uconn.edu/about")
            .urlHash("about456")
            .title("About UConn - Old Title")
            .textContent("Old content")
            .wordCount(2)
            .firstSeenAt(LocalDateTime.now().minusDays(1))
            .lastCrawledAt(LocalDateTime.now().minusDays(1))
            .crawlVersion(1)
            .isCurrent(true)
            .build();

        pageRepository.save(v1);
        pageRepository.flush();

        // When - Mark v1 as not current and create v2
        pageRepository.markAllVersionsNotCurrent("about456");

        Page v2 = Page.builder()
            .url("https://uconn.edu/about")
            .urlHash("about456")
            .title("About UConn - New Title")
            .textContent("Updated content")
            .wordCount(2)
            .firstSeenAt(v1.getFirstSeenAt())  // Keep original first_seen
            .lastCrawledAt(LocalDateTime.now())
            .crawlVersion(2)
            .isCurrent(true)
            .build();

        pageRepository.save(v2);
        pageRepository.flush();

        // Then
        Optional<Page> currentPage = pageRepository.findCurrentByUrlHash("about456");
        assertThat(currentPage).isPresent();
        assertThat(currentPage.get().getCrawlVersion()).isEqualTo(2);
        assertThat(currentPage.get().getTitle()).isEqualTo("About UConn - New Title");
        assertThat(currentPage.get().getIsCurrent()).isTrue();

        Optional<Page> oldVersion = pageRepository.findByUrlHashAndVersion("about456", 1);
        assertThat(oldVersion).isPresent();
        assertThat(oldVersion.get().getTitle()).isEqualTo("About UConn - Old Title");
        assertThat(oldVersion.get().getIsCurrent()).isFalse();

        Optional<Integer> latestVersion = pageRepository.getLatestVersion("about456");
        assertThat(latestVersion).isPresent();
        assertThat(latestVersion.get()).isEqualTo(2);
    }

    @Test
    @DisplayName("Should cascade delete entities, keywords, and categories")
    void shouldCascadeDelete() {
        // Given
        Page page = Page.builder()
            .url("https://uconn.edu/test")
            .urlHash("test789")
            .title("Test Page")
            .textContent("Test content")
            .wordCount(2)
            .firstSeenAt(LocalDateTime.now())
            .lastCrawledAt(LocalDateTime.now())
            .crawlVersion(1)
            .isCurrent(true)
            .build();

        page.addEntity(Entity.builder()
            .entityText("Test Entity")
            .entityType("TEST")
            .build());

        page.addKeyword(Keyword.builder()
            .keyword("test")
            .score(new BigDecimal("0.5"))
            .build());

        page.addCategory(Category.builder()
            .categoryName("Test Category")
            .categoryPath("test.category")
            .build());

        Page saved = pageRepository.save(page);
        Long pageId = saved.getId();
        pageRepository.flush();

        // When
        pageRepository.deleteById(pageId);
        pageRepository.flush();

        // Then
        Optional<Page> deleted = pageRepository.findById(pageId);
        assertThat(deleted).isEmpty();
        // Entities, keywords, and categories should be cascade deleted due to orphanRemoval=true
    }
}
