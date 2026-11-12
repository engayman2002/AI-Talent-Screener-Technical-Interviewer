"use client";

import React, { createContext, useContext, useEffect, useMemo, useRef } from "react";
import {
  createFrontendServices,
  type FrontendServicesBundle,
} from "../../composition/container";

const FrontendServicesContext = createContext<FrontendServicesBundle | null>(null);

export interface AppServicesProviderProps {
  readonly children: React.ReactNode;
  readonly apiBaseUrl?: string;
  readonly wsBaseUrl?: string;
  readonly initialAuthToken?: string | null;
}

export function AppServicesProvider({
  children,
  apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api",
  wsBaseUrl = process.env.NEXT_PUBLIC_WS_BASE_URL ?? "ws://127.0.0.1:8000",
  initialAuthToken = null,
}: AppServicesProviderProps): React.JSX.Element {
  const tokenRef = useRef<string | null>(initialAuthToken);

  const bundle = useMemo(
    () =>
      createFrontendServices({
        apiBaseUrl,
        wsBaseUrl,
        getAuthToken: () => tokenRef.current,
        setAuthToken: (nextToken) => {
          tokenRef.current = nextToken;
        },
      }),
    [apiBaseUrl, wsBaseUrl],
  );

  useEffect(() => {
    void bundle.demoCoordinator.bootstrap();
  }, [bundle]);

  return (
    <FrontendServicesContext.Provider value={bundle}>
      {children}
    </FrontendServicesContext.Provider>
  );
}

export function useFrontendServices(): FrontendServicesBundle {
  const ctx = useContext(FrontendServicesContext);
  if (ctx === null) {
    throw new Error("useFrontendServices must be called inside AppServicesProvider.");
  }
  return ctx;
}
