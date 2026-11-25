import { Link } from "react-router-dom";

/** Real 404 route — replaces the previous silent redirect-to-"/" catch-all. */
export default function NotFound() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-margin-mobile">
      <div className="text-center max-w-md">
        <p className="font-display-lg text-display-lg text-primary">404</p>
        <h1 className="font-headline-md text-headline-md text-on-surface mt-2">Page not found</h1>
        <p className="text-on-surface-variant mt-2">
          The page you're looking for doesn't exist or may have moved.
        </p>
        <Link
          to="/"
          className="inline-block mt-6 bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-lg transition-all"
        >
          Go home
        </Link>
      </div>
    </div>
  );
}
