/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // BLUSSIT brand palette — warm cream + near-black + gold accent.
        ink: "#101114",
        dark: "#0B0C0E",
        cream: "#FFFAF0",
        gold: "#F5B51B",
        goldDeep: "#9C6C00",
        goldSoft: "#FFF6D8",
        muted: "#5F6064",
        border: "#E8E4D8",
      },
      fontFamily: {
        display: ["Manrope", "Inter", "sans-serif"],
        body: ["Inter", "sans-serif"],
      },
      maxWidth: {
        content: "1280px",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: 0, transform: "translateY(24px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        float: {
          "50%": { transform: "translateY(-10px)" },
        },
      },
      animation: {
        fadeUp: "fadeUp 0.7s ease-out forwards",
        float: "float 5s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
