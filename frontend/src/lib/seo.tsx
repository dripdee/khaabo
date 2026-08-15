import { Helmet } from "react-helmet-async";

import { SEO_BASE_URL } from "@/lib/jsonld";

const SITE_NAME = "Khaabo";

export interface SeoProps {
  title: string;
  description: string;
  /** Path only, e.g. `/dish/chicken-momo`. Combined with the site origin. */
  canonicalPath?: string;
  image?: string;
  /** JSON-LD. Only pass it when the data is real — never for unranked entities. */
  jsonLd?: Record<string, unknown> | Record<string, unknown>[];
  noIndex?: boolean;
}

/**
 * Per-route metadata.
 *
 * Kept as a component (rather than a hook writing to document.head) so the same
 * markup works unchanged if the app later moves to SSR — react-helmet-async collects
 * these on the server too.
 */
export function Seo({
  title,
  description,
  canonicalPath,
  image,
  jsonLd,
  noIndex = false,
}: SeoProps) {
  const fullTitle = title.includes(SITE_NAME) ? title : `${title} · ${SITE_NAME}`;
  const canonical = canonicalPath ? `${SEO_BASE_URL}${canonicalPath}` : undefined;

  return (
    <Helmet>
      <title>{fullTitle}</title>
      <meta name="description" content={description} />
      {canonical && <link rel="canonical" href={canonical} />}
      {noIndex && <meta name="robots" content="noindex, nofollow" />}

      <meta property="og:type" content="website" />
      <meta property="og:site_name" content={SITE_NAME} />
      <meta property="og:title" content={fullTitle} />
      <meta property="og:description" content={description} />
      {canonical && <meta property="og:url" content={canonical} />}
      {image && <meta property="og:image" content={image} />}

      <meta name="twitter:card" content={image ? "summary_large_image" : "summary"} />
      <meta name="twitter:title" content={fullTitle} />
      <meta name="twitter:description" content={description} />

      {jsonLd && <script type="application/ld+json">{JSON.stringify(jsonLd)}</script>}
    </Helmet>
  );
}
