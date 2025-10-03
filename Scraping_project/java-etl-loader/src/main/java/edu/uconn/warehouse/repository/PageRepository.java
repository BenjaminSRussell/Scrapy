package edu.uconn.warehouse.repository;

import edu.uconn.warehouse.entity.Page;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.Optional;

/**
 * Repository for Page entities.
 * Provides database access methods with support for versioning.
 */
@Repository
public interface PageRepository extends JpaRepository<Page, Long> {

    /**
     * Find the current version of a page by URL hash
     */
    @Query("SELECT p FROM Page p WHERE p.urlHash = :urlHash AND p.isCurrent = true")
    Optional<Page> findCurrentByUrlHash(@Param("urlHash") String urlHash);

    /**
     * Find a specific version of a page
     */
    @Query("SELECT p FROM Page p WHERE p.urlHash = :urlHash AND p.crawlVersion = :version")
    Optional<Page> findByUrlHashAndVersion(
        @Param("urlHash") String urlHash,
        @Param("version") Integer version
    );

    /**
     * Mark all versions of a page as not current
     */
    @Modifying
    @Query("UPDATE Page p SET p.isCurrent = false WHERE p.urlHash = :urlHash")
    int markAllVersionsNotCurrent(@Param("urlHash") String urlHash);

    /**
     * Get the latest crawl version for a URL
     */
    @Query("SELECT MAX(p.crawlVersion) FROM Page p WHERE p.urlHash = :urlHash")
    Optional<Integer> getLatestVersion(@Param("urlHash") String urlHash);

    /**
     * Count current pages
     */
    @Query("SELECT COUNT(p) FROM Page p WHERE p.isCurrent = true")
    long countCurrentPages();
}
