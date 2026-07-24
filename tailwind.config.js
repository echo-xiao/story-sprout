/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    // Frontend was moved from frontend/src/* to the repo root during the
    // Vercel consolidation (root app/, components/, lib/ — no src/ dir, to
    // dodge the Python src/). These globs must match the new locations or
    // Tailwind scans nothing and purges every utility class, shipping a
    // ~6KB reset-only stylesheet that renders the whole app unstyled.
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontSize: {
        xs: ["0.75rem", { lineHeight: "1.26" }],
        sm: ["0.875rem", { lineHeight: "1.26" }],
        base: ["1rem", { lineHeight: "1.26" }],
        lg: ["1.125rem", { lineHeight: "1.26" }],
        xl: ["1.25rem", { lineHeight: "1.26" }],
        "2xl": ["1.5rem", { lineHeight: "1.26" }],
      },
      fontFamily: {
        display: ['"Comic Neue"', '"Comic Sans MS"', "cursive"],
        body: ['"Nunito"', '"Comic Neue"', "sans-serif"],
      },
      colors: {
        cream: "#FFF8F0",
        peach: "#FFE5D9",
        sage: "#D4E7C5",
        sky: "#BDE0FE",
        lavender: "#E2D1F9",
        coral: "#FF9B85",
        sunshine: "#FFD966",
      },
      animation: {
        "page-flip": "pageFlip 0.6s ease-in-out",
        "fade-in": "fadeIn 0.5s ease-out",
        "slide-up": "slideUp 0.4s ease-out",
        float: "float 3s ease-in-out infinite",
      },
      keyframes: {
        pageFlip: {
          "0%": { transform: "rotateY(0deg)" },
          "100%": { transform: "rotateY(-180deg)" },
        },
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        slideUp: {
          "0%": { opacity: "0", transform: "translateY(20px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-10px)" },
        },
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
