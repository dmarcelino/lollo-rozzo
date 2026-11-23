import logging

from lollo_vault.logs import SecretFilter

from .conftest import SECRET


def test_log_filter_redacts_values_while_in_use(caplog):
    f = SecretFilter()
    logger = logging.getLogger("test.redaction")
    logger.addFilter(f)
    try:
        with caplog.at_level(logging.INFO, logger="test.redaction"):
            with f.guarding([SECRET, "PT"]):
                logger.info("oops %s in PT", SECRET)
                try:
                    raise ValueError(SECRET)
                except ValueError:
                    logger.exception("failed")
            logger.info("after %s", "done")
    finally:
        logger.removeFilter(f)
    assert SECRET not in caplog.text
    assert "[REDACTED] in PT" in caplog.text and "after done" in caplog.text
