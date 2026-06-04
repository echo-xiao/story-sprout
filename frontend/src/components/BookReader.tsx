"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { PictureBook } from "@/types";

interface Props {
  book: PictureBook;
  onBack: () => void;
}

export function BookReader({ book, onBack }: Props) {
  const [currentPage, setCurrentPage] = useState(-1); // -1 = cover
  const [direction, setDirection] = useState(0);
  const totalPages = book.pages.length;

  const goNext = useCallback(() => {
    if (currentPage < totalPages - 1) {
      setDirection(1);
      setCurrentPage((p) => p + 1);
    }
  }, [currentPage, totalPages]);

  const goPrev = useCallback(() => {
    if (currentPage > -1) {
      setDirection(-1);
      setCurrentPage((p) => p - 1);
    }
  }, [currentPage]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowRight" || e.key === " ") goNext();
      if (e.key === "ArrowLeft") goPrev();
    },
    [goNext, goPrev]
  );

  const variants = {
    enter: (dir: number) => ({
      x: dir > 0 ? 500 : -500,
      opacity: 0,
      rotateY: dir > 0 ? 15 : -15,
    }),
    center: {
      x: 0,
      opacity: 1,
      rotateY: 0,
    },
    exit: (dir: number) => ({
      x: dir > 0 ? -500 : 500,
      opacity: 0,
      rotateY: dir > 0 ? -15 : 15,
    }),
  };

  const page = currentPage >= 0 ? book.pages[currentPage] : null;

  return (
    <div
      className="animate-fade-in outline-none"
      tabIndex={0}
      onKeyDown={handleKeyDown}
    >
      {/* Top bar */}
      <div className="flex items-center justify-between mb-6">
        <button onClick={onBack} className="btn-secondary text-sm">
          &larr; Back
        </button>
        <h2 className="font-display text-xl font-bold text-gray-800">
          {book.title}
        </h2>
        <div className="text-sm text-gray-400">
          {currentPage === -1
            ? "Cover"
            : `Page ${currentPage + 1} of ${totalPages}`}
        </div>
      </div>

      {/* Book */}
      <div className="relative max-w-4xl mx-auto" style={{ perspective: 1200 }}>
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={currentPage}
            custom={direction}
            variants={variants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ duration: 0.4, ease: "easeInOut" }}
            className="book-page rounded-3xl overflow-hidden shadow-2xl"
          >
            {currentPage === -1 ? (
              /* Cover Page */
              <div className="aspect-square md:aspect-[4/3] flex flex-col items-center justify-center p-12 bg-gradient-to-br from-peach via-cream to-lavender">
                <div className="text-center space-y-6">
                  <span className="text-7xl">📖</span>
                  <h1 className="font-display text-4xl md:text-5xl font-bold text-gray-800">
                    {book.title}
                  </h1>
                  <p className="text-gray-600 text-lg">
                    A picture book for ages {book.config.age_group}
                  </p>
                  <p className="text-gray-400 text-sm">Tap or click to start reading</p>
                </div>
              </div>
            ) : page ? (
              /* Content Page */
              <div className="aspect-square md:aspect-[4/3] relative">
                {/* Illustration */}
                <div className="absolute inset-0">
                  {page.illustration_url ? (
                    <img
                      src={page.illustration_url}
                      alt={`Page ${page.page_number} illustration`}
                      className="w-full h-full object-cover"
                    />
                  ) : (
                    <div className="w-full h-full bg-gradient-to-br from-sky/30 to-lavender/30 flex items-center justify-center">
                      <span className="text-6xl opacity-30">🎨</span>
                    </div>
                  )}
                </div>

                {/* Text overlay */}
                <div className="absolute bottom-0 left-0 right-0 p-6 md:p-10">
                  <div className="bg-white/90 backdrop-blur-sm rounded-2xl p-5 md:p-8 shadow-lg">
                    <p className="font-display text-lg md:text-2xl text-gray-800 leading-relaxed text-center">
                      {page.text}
                    </p>
                  </div>
                </div>

                {/* Page number */}
                <div className="absolute bottom-2 right-4 text-xs text-gray-400 bg-white/60 px-2 py-1 rounded-full">
                  {currentPage + 1}
                </div>
              </div>
            ) : null}
          </motion.div>
        </AnimatePresence>

        {/* Navigation arrows */}
        <button
          onClick={goPrev}
          disabled={currentPage === -1}
          className={`absolute left-0 top-1/2 -translate-y-1/2 -translate-x-14
                     w-10 h-10 rounded-full bg-white shadow-lg flex items-center justify-center
                     transition-all hover:shadow-xl hover:scale-110
                     ${currentPage === -1 ? "opacity-30 cursor-not-allowed" : "opacity-70 hover:opacity-100"}`}
        >
          ◀
        </button>
        <button
          onClick={goNext}
          disabled={currentPage === totalPages - 1}
          className={`absolute right-0 top-1/2 -translate-y-1/2 translate-x-14
                     w-10 h-10 rounded-full bg-white shadow-lg flex items-center justify-center
                     transition-all hover:shadow-xl hover:scale-110
                     ${currentPage === totalPages - 1 ? "opacity-30 cursor-not-allowed" : "opacity-70 hover:opacity-100"}`}
        >
          ▶
        </button>
      </div>

      {/* Page dots */}
      <div className="flex justify-center gap-2 mt-6">
        <button
          onClick={() => {
            setDirection(currentPage > -1 ? -1 : 1);
            setCurrentPage(-1);
          }}
          className={`w-3 h-3 rounded-full transition-all ${
            currentPage === -1 ? "bg-coral scale-125" : "bg-gray-300 hover:bg-gray-400"
          }`}
        />
        {book.pages.map((_, idx) => (
          <button
            key={idx}
            onClick={() => {
              setDirection(idx > currentPage ? 1 : -1);
              setCurrentPage(idx);
            }}
            className={`w-3 h-3 rounded-full transition-all ${
              currentPage === idx
                ? "bg-coral scale-125"
                : "bg-gray-300 hover:bg-gray-400"
            }`}
          />
        ))}
      </div>

      {/* QA Results */}
      {book.qa_results && (
        <div className="mt-8 card max-w-4xl mx-auto">
          <h3 className="font-display text-lg font-bold mb-4">
            Quality Report
            <span
              className={`ml-2 text-sm px-3 py-1 rounded-full ${
                book.qa_results.passes
                  ? "bg-sage text-gray-800"
                  : "bg-red-100 text-red-700"
              }`}
            >
              {book.qa_results.passes ? "All Passed" : "Issues Found"}
            </span>
          </h3>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <QACard
              title="Safety"
              passed={book.qa_results.safety.is_safe}
              score={book.qa_results.safety.overall_score}
            />
            <QACard
              title="Readability"
              passed={book.qa_results.readability.passes}
            />
            <QACard
              title="Coverage"
              passed={book.qa_results.coverage.coverage_score >= 0.5}
              score={book.qa_results.coverage.coverage_score}
            />
            <QACard
              title="Accuracy"
              passed={book.qa_results.hallucination.is_acceptable}
              score={1 - book.qa_results.hallucination.hallucination_score}
            />
          </div>

          {!book.qa_results.passes && (
            <p className="text-sm text-gray-500 mt-3">
              {book.qa_results.summary}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function QACard({
  title,
  passed,
  score,
}: {
  title: string;
  passed: boolean;
  score?: number;
}) {
  return (
    <div
      className={`p-3 rounded-xl text-center ${
        passed ? "bg-sage/30" : "bg-red-50"
      }`}
    >
      <div className="text-lg">{passed ? "✅" : "⚠️"}</div>
      <div className="font-semibold text-sm">{title}</div>
      {score !== undefined && (
        <div className="text-xs text-gray-500 mt-1">
          {(score * 100).toFixed(0)}%
        </div>
      )}
    </div>
  );
}
