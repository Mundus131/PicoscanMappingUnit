"""Suppress only per-packet progress, never parser/transport diagnostics."""
import logging
import re


class SegmentProgressFilter(logging.Filter):
    def filter(self, record):
        return not (record.levelno <= logging.INFO and
                    re.fullmatch(r'Received segment [0-9]+\.', record.getMessage()))


def quiet_segment_progress(module):
    logger = getattr(module, 'logger', None)
    if isinstance(logger, logging.Logger):
        if not any(isinstance(f, SegmentProgressFilter) for f in logger.filters):
            logger.addFilter(SegmentProgressFilter())
