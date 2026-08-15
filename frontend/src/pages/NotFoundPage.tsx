import { Link } from "react-router-dom";

import { Button } from "@/components/Button";
import { Seo } from "@/lib/seo";

export default function NotFoundPage() {
  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <Seo title="Not found" description="That page does not exist." noIndex />
      <p className="text-xs uppercase tracking-[0.2em] text-subtle">404</p>
      <h1 className="mt-4 font-display text-hero text-text">Nothing here</h1>
      <p className="mt-4 text-muted">
        That page does not exist. The food, however, still does.
      </p>
      <Link to="/" className="mt-8 inline-block">
        <Button>Find something to eat</Button>
      </Link>
    </div>
  );
}
