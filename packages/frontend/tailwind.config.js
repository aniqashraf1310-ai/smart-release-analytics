/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Vibrant violet brand colour, used with a violet -> pink -> amber gradient.
        primary: {
          DEFAULT: '#7C3AED',
          50: '#F5F3FF',
          100: '#EDE9FE',
          200: '#DDD6FE',
          300: '#C4B5FD',
          600: '#7C3AED',
          700: '#6D28D9',
          800: '#5B21B6',
        },
        accent: { DEFAULT: '#EC4899', 50: '#FDF2F8' },
        neutral: '#64748B',
      },
    },
  },
  plugins: [],
};
