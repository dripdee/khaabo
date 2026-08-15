import { useCallback, useState } from "react";

export interface Coords {
  lat: number;
  lng: number;
}

interface GeolocationState {
  coords: Coords | null;
  status: "idle" | "prompting" | "granted" | "denied" | "unavailable";
  error: string | null;
}

const STORAGE_KEY = "khaabo:coords";

/**
 * Opt-in geolocation.
 *
 * Never requested on mount: the browser prompt is only shown when the user asks for
 * "near me", so the first visit is not interrupted by a permission dialog. The last
 * granted position is cached in sessionStorage so a page change does not re-prompt.
 */
export function useGeolocation() {
  const [state, setState] = useState<GeolocationState>(() => {
    const cached = sessionStorage.getItem(STORAGE_KEY);
    if (cached) {
      try {
        return { coords: JSON.parse(cached) as Coords, status: "granted", error: null };
      } catch {
        // fall through to idle
      }
    }
    return { coords: null, status: "idle", error: null };
  });

  const request = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setState({ coords: null, status: "unavailable", error: "Location is not supported here" });
      return;
    }

    setState((previous) => ({ ...previous, status: "prompting", error: null }));

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coords: Coords = {
          lat: Number(position.coords.latitude.toFixed(5)),
          lng: Number(position.coords.longitude.toFixed(5)),
        };
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(coords));
        setState({ coords, status: "granted", error: null });
      },
      (error) => {
        setState({
          coords: null,
          status: error.code === error.PERMISSION_DENIED ? "denied" : "unavailable",
          error:
            error.code === error.PERMISSION_DENIED
              ? "Location permission was denied. You can still search by area."
              : "Could not determine your location.",
        });
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 300000 },
    );
  }, []);

  const clear = useCallback(() => {
    sessionStorage.removeItem(STORAGE_KEY);
    setState({ coords: null, status: "idle", error: null });
  }, []);

  return { ...state, request, clear };
}
