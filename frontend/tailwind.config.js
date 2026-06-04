/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
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
