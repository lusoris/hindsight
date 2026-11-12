import React from "react";
import Link from "@docusaurus/Link";
import type { PropBlogPostContent } from "@docusaurus/plugin-content-blog";
import styles from "./BlogCard.module.css";

interface BlogCardProps {
  content: PropBlogPostContent;
  categoryLabel?: string | null;
  viewTransitionName?: string;
}

/** Format an ISO blog post date as a short US English label (e.g. "Nov 12, 2026"). */
function formatDate(dateString: string): string {
  const date = new Date(dateString);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/**
 * Preview card for a blog post on the blog index and tag archive pages.
 *
 * `categoryLabel` renders the category pill above the title when truthy.
 * `viewTransitionName` sets `style.viewTransitionName` on the card link only when
 * provided, so duplicate posts on the same page (such as Featured pins alongside
 * the main grid) or static tag archives can omit it.
 */
export default function BlogCard({
  content,
  categoryLabel,
  viewTransitionName,
}: BlogCardProps): React.ReactElement {
  const { metadata, assets } = content;
  const { title, description, date, readingTime, permalink, frontMatter } = metadata;
  const image = assets.image ?? frontMatter.image ?? "/img/blog-default.jpg";

  return (
    <Link
      to={permalink}
      className={styles.card}
      style={viewTransitionName ? { viewTransitionName } : undefined}
    >
      <div className={styles.cardImageWrapper}>
        {image ? (
          <img src={image} alt={title} className={styles.cardImage} />
        ) : (
          <div className={styles.cardImagePlaceholder} />
        )}
      </div>
      <div className={styles.cardBody}>
        {categoryLabel ? <span className={styles.cardBadge}>{categoryLabel}</span> : null}
        <h2 className={styles.cardTitle}>{title}</h2>
        {description && <p className={styles.cardDescription}>{description}</p>}
        <div className={styles.cardFooter}>
          <span className={styles.cardDate}>{formatDate(date)}</span>
          {readingTime !== undefined && (
            <span className={styles.cardReadTime}>{Math.ceil(readingTime)} min read</span>
          )}
        </div>
      </div>
    </Link>
  );
}
