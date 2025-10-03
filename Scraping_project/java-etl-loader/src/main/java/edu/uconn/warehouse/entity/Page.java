package edu.uconn.warehouse.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.Type;
import io.hypersistence.utils.hibernate.type.json.JsonBinaryType;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * JPA Entity for the pages table.
 * Represents a scraped web page with versioning support.
 */
@Entity
@Table(name = "pages", indexes = {
    @Index(name = "idx_pages_url_hash", columnList = "url_hash"),
    @Index(name = "idx_pages_is_current", columnList = "is_current"),
    @Index(name = "idx_pages_last_crawled", columnList = "last_crawled_at"),
    @Index(name = "idx_pages_crawl_version", columnList = "crawl_version")
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Page {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "page_id")
    private Long id;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String url;

    @Column(name = "url_hash", nullable = false, length = 64)
    private String urlHash;

    @Column(columnDefinition = "TEXT")
    private String title;

    @Column(name = "text_content", columnDefinition = "TEXT")
    private String textContent;

    @Column(name = "word_count")
    @Builder.Default
    private Integer wordCount = 0;

    @Column(name = "first_seen_at", nullable = false)
    private LocalDateTime firstSeenAt;

    @Column(name = "last_crawled_at", nullable = false)
    private LocalDateTime lastCrawledAt;

    @Column(name = "crawl_version")
    @Builder.Default
    private Integer crawlVersion = 1;

    @Column(name = "is_current")
    @Builder.Default
    private Boolean isCurrent = true;

    @Column(columnDefinition = "jsonb")
    private String metadata;

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    @Builder.Default
    private List<Entity> entities = new ArrayList<>();

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    @Builder.Default
    private List<Keyword> keywords = new ArrayList<>();

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    @Builder.Default
    private List<Category> categories = new ArrayList<>();

    /**
     * Helper method to add an entity to this page
     */
    public void addEntity(Entity entity) {
        entities.add(entity);
        entity.setPage(this);
    }

    /**
     * Helper method to add a keyword to this page
     */
    public void addKeyword(Keyword keyword) {
        keywords.add(keyword);
        keyword.setPage(this);
    }

    /**
     * Helper method to add a category to this page
     */
    public void addCategory(Category category) {
        categories.add(category);
        category.setPage(this);
    }

    @PrePersist
    protected void onCreate() {
        if (firstSeenAt == null) {
            firstSeenAt = LocalDateTime.now();
        }
        if (lastCrawledAt == null) {
            lastCrawledAt = LocalDateTime.now();
        }
    }
}
