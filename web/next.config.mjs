/** Статическая сборка: раздаётся nginx в ЦОДе РК, API — отдельный сервис. */
const nextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
