"""Fake text generation for mock ASR."""

import random


class TextGenerator:
    """
    Generates realistic-looking fake transcription text.

    Uses a vocabulary of common words to create natural-looking output.
    Word count is proportional to audio byte length.
    """

    # Common English words for generating fake transcriptions
    VOCABULARY = [
        "the", "be", "to", "of", "and", "a", "in", "that", "have", "I",
        "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
        "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
        "or", "an", "will", "my", "one", "all", "would", "there", "their", "what",
        "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
        "when", "make", "can", "like", "time", "no", "just", "him", "know", "take",
        "people", "into", "year", "your", "good", "some", "could", "them", "see", "other",
        "than", "then", "now", "look", "only", "come", "its", "over", "think", "also",
        "back", "after", "use", "two", "how", "our", "work", "first", "well", "way",
        "even", "new", "want", "because", "any", "these", "give", "day", "most", "us",
        "is", "was", "are", "been", "has", "had", "did", "does", "being", "were",
        "very", "much", "more", "many", "such", "long", "great", "little", "own", "other",
        "old", "right", "big", "high", "different", "small", "large", "next", "early", "young",
        "important", "few", "public", "bad", "same", "able", "last", "sure", "real", "best",
        "better", "still", "never", "should", "world", "life", "man", "too", "under", "here",
        "need", "house", "home", "hand", "school", "place", "while", "away", "keep", "let",
        "begin", "seem", "help", "show", "hear", "play", "run", "move", "live", "believe",
        "hold", "bring", "happen", "must", "write", "provide", "sit", "stand", "lose", "pay",
        "meet", "include", "continue", "set", "learn", "change", "lead", "understand", "watch", "follow",
        "stop", "create", "speak", "read", "allow", "add", "spend", "grow", "open", "walk",
        "win", "offer", "remember", "love", "consider", "appear", "buy", "wait", "serve", "die",
        "send", "expect", "build", "stay", "fall", "cut", "reach", "kill", "remain", "suggest",
        "raise", "pass", "sell", "require", "report", "decide", "pull", "develop", "thank", "carry",
    ]

    def __init__(self, bytes_per_word: int = 12800):
        """
        Initialize text generator.

        Args:
            bytes_per_word: Number of audio bytes that correspond to one word
        """
        self.bytes_per_word = bytes_per_word

    def generate(self, audio_bytes: int) -> str:
        """
        Generate fake text proportional to audio length.

        Args:
            audio_bytes: Number of audio bytes

        Returns:
            Generated fake transcription text
        """
        word_count = max(1, audio_bytes // self.bytes_per_word)
        words = random.choices(self.VOCABULARY, k=word_count)
        return " ".join(words)

    def generate_words(self, word_count: int) -> str:
        """
        Generate a specific number of words.

        Args:
            word_count: Number of words to generate

        Returns:
            Generated fake transcription text
        """
        if word_count <= 0:
            return ""
        words = random.choices(self.VOCABULARY, k=word_count)
        return " ".join(words)
