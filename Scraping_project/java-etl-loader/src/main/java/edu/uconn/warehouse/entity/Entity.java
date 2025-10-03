package edu.uconn.warehouse.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * JPA Entity for the entities table.
 * Represents a named entity extracted from a page.
 */
@jakarta.persistence.Entity
@Table(name = "entities", indexes = {
    @Index(name = "idx_entities_page_id", columnList = "page_id"),
    @Index(name = "idx_entities_type", columnList = "entity_type"),
    @Index(name = "idx_entities_text", columnList = "entity_text")
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Entity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "entity_id")
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "page_id", nullable = false)
    private Page page;

    @Column(name = "entity_text", nullable = false, columnDefinition = "TEXT")
    private String entityText;

    @Column(name = "entity_type", length = 50)
    private String entityType;

    @Column(precision = 3, scale = 2)
    private BigDecimal confidence;

    @Column(name = "created_at", nullable = false)
    @Builder.Default
    private LocalDateTime createdAt = LocalDateTime.now();

    @PrePersist
    protected void onCreate() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
    }
}
