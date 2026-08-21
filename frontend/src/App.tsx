import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/AppLayout";
import { ListSkeleton } from "@/components/Skeleton";

// Priority screens load eagerly; the rest are split so the first paint on the core
// loop (home → dish → map) is not paying for the admin dashboard.
import HomePage from "@/pages/HomePage";
import DishPage from "@/pages/DishPage";
import DishMapPage from "@/pages/DishMapPage";

const SearchPage = lazy(() => import("@/pages/SearchPage"));
const RestaurantPage = lazy(() => import("@/pages/RestaurantPage"));
const CityMapPage = lazy(() => import("@/pages/CityMapPage"));
const TrendingPage = lazy(() => import("@/pages/TrendingPage"));
const ProfilePage = lazy(() => import("@/pages/ProfilePage"));
const BookmarksPage = lazy(() => import("@/pages/BookmarksPage"));
const ReviewSubmitPage = lazy(() => import("@/pages/ReviewSubmitPage"));
const AuthPage = lazy(() => import("@/pages/AuthPage"));
const AdminPage = lazy(() => import("@/pages/AdminPage"));
const NotFoundPage = lazy(() => import("@/pages/NotFoundPage"));

function RouteFallback() {
  return (
    <div className="mx-auto max-w-content px-4 py-12">
      <ListSkeleton count={3} />
    </div>
  );
}

export function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="dish/:slug" element={<DishPage />} />
        <Route path="dish/:slug/map" element={<DishMapPage />} />

        <Route
          path="search"
          element={
            <Suspense fallback={<RouteFallback />}>
              <SearchPage />
            </Suspense>
          }
        />
        <Route
          path="restaurant/:id"
          element={
            <Suspense fallback={<RouteFallback />}>
              <RestaurantPage />
            </Suspense>
          }
        />
        <Route
          path="map"
          element={
            <Suspense fallback={<RouteFallback />}>
              <CityMapPage />
            </Suspense>
          }
        />
        <Route
          path="trending"
          element={
            <Suspense fallback={<RouteFallback />}>
              <TrendingPage />
            </Suspense>
          }
        />
        <Route
          path="u/:username"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ProfilePage />
            </Suspense>
          }
        />
        <Route
          path="bookmarks"
          element={
            <Suspense fallback={<RouteFallback />}>
              <BookmarksPage />
            </Suspense>
          }
        />
        <Route
          path="review/new"
          element={
            <Suspense fallback={<RouteFallback />}>
              <ReviewSubmitPage />
            </Suspense>
          }
        />
        <Route
          path="auth"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AuthPage />
            </Suspense>
          }
        />
        <Route
          path="admin"
          element={
            <Suspense fallback={<RouteFallback />}>
              <AdminPage />
            </Suspense>
          }
        />
        <Route
          path="*"
          element={
            <Suspense fallback={<RouteFallback />}>
              <NotFoundPage />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  );
}
