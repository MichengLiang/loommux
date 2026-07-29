//! Fallible conversion and monotonic deadline construction for authored timeouts.

use std::time::Duration;

#[must_use]
pub fn positive_duration(seconds: f64) -> Option<Duration> {
    if !seconds.is_finite() || seconds <= 0.0 {
        return None;
    }
    Duration::try_from_secs_f64(seconds)
        .ok()
        .filter(|duration| !duration.is_zero())
}

#[must_use]
pub fn from_now(duration: Duration) -> Option<tokio::time::Instant> {
    tokio::time::Instant::now().checked_add(duration)
}

#[cfg(test)]
mod tests {
    use super::{from_now, positive_duration};

    #[test]
    fn authored_seconds_reject_nonpositive_rounded_zero_and_overflow() {
        for invalid in [f64::NAN, f64::INFINITY, -1.0, 0.0, 1e-300, 1e300] {
            assert!(positive_duration(invalid).is_none());
        }
        let ordinary = positive_duration(0.25).unwrap();
        assert_eq!(ordinary.as_millis(), 250);
        assert!(from_now(ordinary).is_some());
    }
}
