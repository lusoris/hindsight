import React from 'react';
import Link from '@docusaurus/Link';
import Layout from '@theme/Layout';
import type {Props} from '@theme/BlogTagsPostsPage';
import BlogCard from '@site/src/components/BlogCard';
import styles from '../BlogListPage/styles.module.css';

const TAG_LABELS: Record<string, {title: string; subtitle: string}> = {
  'hindsight-cloud': {
    title: 'Hindsight Cloud',
    subtitle: 'News and updates from Hindsight Cloud',
  },
  'deep-dive': {
    title: 'Deep Dives',
    subtitle: 'Architecture, engineering, and benchmarks',
  },
  'release': {
    title: 'Announcements & Releases',
    subtitle: 'New versions, milestones, and product news',
  },
  'tutorial': {
    title: 'Tutorials & Integrations',
    subtitle: 'How to use Hindsight with your stack',
  },
};

export default function BlogTagsPostsPage({tag, items, listMetadata}: Props): React.ReactElement {
  const {totalPages, page, nextPage, previousPage} = listMetadata;
  const tagInfo = TAG_LABELS[tag.label] ?? {title: tag.label, subtitle: undefined};

  return (
    <Layout title={tagInfo.title} description={tagInfo.subtitle}>
      <main className={styles.blogPage}>
        <header className={styles.header}>
          <h1 className={styles.headerTitle}>{tagInfo.title}</h1>
          {tagInfo.subtitle && <p className={styles.headerSubtitle}>{tagInfo.subtitle}</p>}
        </header>

        <div className={styles.grid}>
          {items.map(({content: BlogPostContent}) => (
            <BlogCard key={BlogPostContent.metadata.permalink} content={BlogPostContent} />
          ))}
        </div>

        {totalPages > 1 && (
          <nav className={styles.pagination}>
            {previousPage && (
              <Link to={previousPage} className={styles.paginationButton}>
                ← Previous
              </Link>
            )}
            <span className={styles.paginationInfo}>
              Page {page} of {totalPages}
            </span>
            {nextPage && (
              <Link to={nextPage} className={styles.paginationButton}>
                Next →
              </Link>
            )}
          </nav>
        )}
      </main>
    </Layout>
  );
}
