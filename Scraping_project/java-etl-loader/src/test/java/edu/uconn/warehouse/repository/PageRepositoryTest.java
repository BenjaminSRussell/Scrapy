package edu.uconn.warehouse.repository;

import edu.uconn.warehouse.entity.Page;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.test.context.ActiveProfiles;

import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Unit tests for PageRepository.
 * Uses in-memory H2 database for fast testing.
 */
@DataJpaTest
@ActiveProfiles("test")
@DisplayName("PageRepository Unit Tests")
class PageRepositoryTest {

    @Autowired
    private TestEntityManager entityManager;

    @Autowired
    private PageRepository pageRepository;

    @Test
    @DisplayName("Should find current page by URL hash")
    void shouldFindCurrentPageByUrlHash() {
        // Given
        Page page = Page.builder()
            .url("https://uconn.edu/test")
            .urlHash("abc123")
            .title("Test Page")
            .textContent("Test content")
            .wordCount(2)
            .firstSeenAt(LocalDateTime.now())
            .lastCrawledAt(LocalDateTime.now())
            .crawlVersion(1)
            .isCurrent(true)
            .build();

        entityManager.persist(page);
        entityManager.flush();

        // When
        Optional<Page> found = pageRepository.findCurrentByUrlHash("abc123");

        // Then
        assertThat(found).isPresent();
        assertThat(found.get().getUrl()).isEqualTo("https://uconn.edu/test");
        assertThat(found.get().getIsCurrent()).isTrue();
    }

    @Test
    @DisplayName("Should not find page that is not current")
    void shouldNotFindNonCurrentPage() {
        // Given
        Page oldPage = Page.builder()
            .url("https://uconn.edu/test")
            .urlHash("abc123")
            .title("Old Version")
            .firstSeenAt(LocalDateTime.now().minusDays(1))
            .lastCrawledAt(LocalDateTime.now().minusDays(1))
            .crawlVersion(1)
            .isCurrent(false)
            .build();

        entityManager.persist(oldPage);
        entityManager.flush();

        // When
        Optional<Page> found = pageRepository.findCurrentByUrlHash("abc123");

        // Then
        assertThat(found).isEmpty();
    }

    @Test
    @DisplayName("Should find page by URL hash and version")
    void shouldFindPageByUrlHashAndVersion() {
        // Given
        Page v1 = createPage("abc123", 1, false);
        Page v2 = createPage("abc123", 2, true);

        entityManager.persist(v1);
        entityManager.persist(v2);
        entityManager.flush();

        // When
        Optional<Page> found = pageRepository.findByUrlHashAndVersion("abc123", 1);

        // Then
        assertThat(found).isPresent();
        assertThat(found.get().getCrawlVersion()).isEqualTo(1);
        assertThat(found.get().getIsCurrent()).isFalse();
    }

    @Test
    @DisplayName("Should get latest version number")
    void shouldGetLatestVersion() {
        // Given
        entityManager.persist(createPage("abc123", 1, false));
        entityManager.persist(createPage("abc123", 2, false));
        entityManager.persist(createPage("abc123", 3, true));
        entityManager.flush();

        // When
        Optional<Integer> latestVersion = pageRepository.getLatestVersion("abc123");

        // Then
        assertThat(latestVersion).isPresent();
        assertThat(latestVersion.get()).isEqualTo(3);
    }

    @Test
    @DisplayName("Should mark all versions as not current")
    void shouldMarkAllVersionsNotCurrent() {
        // Given
        entityManager.persist(createPage("abc123", 1, false));
        entityManager.persist(createPage("abc123", 2, true));
        entityManager.flush();

        // When
        int updated = pageRepository.markAllVersionsNotCurrent("abc123");

        // Then
        assertThat(updated).isEqualTo(2);

        Optional<Page> currentPage = pageRepository.findCurrentByUrlHash("abc123");
        assertThat(currentPage).isEmpty();
    }

    @Test
    @DisplayName("Should count only current pages")
    void shouldCountOnlyCurrentPages() {
        // Given
        entityManager.persist(createPage("hash1", 1, true));
        entityManager.persist(createPage("hash2", 1, false));
        entityManager.persist(createPage("hash2", 2, true));
        entityManager.persist(createPage("hash3", 1, true));
        entityManager.flush();

        // When
        long count = pageRepository.countCurrentPages();

        // Then
        assertThat(count).isEqualTo(3);
    }

    private Page createPage(String urlHash, int version, boolean isCurrent) {
        return Page.builder()
            .url("https://uconn.edu/" + urlHash)
            .urlHash(urlHash)
            .title("Page " + urlHash + " v" + version)
            .textContent("Content")
            .wordCount(1)
            .firstSeenAt(LocalDateTime.now().minusDays(version))
            .lastCrawledAt(LocalDateTime.now())
            .crawlVersion(version)
            .isCurrent(isCurrent)
            .build();
    }
}
