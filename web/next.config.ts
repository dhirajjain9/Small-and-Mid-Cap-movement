import type { NextConfig } from "next";

const config: NextConfig = {
  headers: async () => [
    // data refreshes daily; let the CDN cache it briefly
    { source: "/data/:file*", headers: [{ key: "Cache-Control", value: "public, max-age=300, must-revalidate" }] },
  ],
};

export default config;
