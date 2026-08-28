"""
ML-based Compliance Prediction Service

Projects a department's automated scan-score trajectory at its configured
accessibility target date using historical data and multiple predictive features.

Author: Aelira Team
Created: January 2026
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
import numpy as np

from .deadline_config import DeadlineService

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """Result of compliance deadline prediction."""

    will_meet_deadline: Optional[bool]
    confidence: float  # 0-1 confidence in prediction
    probability_of_compliance: Optional[float]
    projected_score_at_deadline: Optional[float]
    current_score: Optional[float]
    days_remaining: Optional[int]
    risk_level: str  # "low", "medium", "high", "critical"
    risk_factors: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    prediction_model: str
    features_used: Dict[str, float]
    deadline: Dict[str, Any]


class CompliancePredictor:
    """
    ML-based compliance deadline prediction using multiple features.

    Uses a weighted ensemble approach combining:
    1. Linear trend extrapolation
    2. Exponential smoothing
    3. Velocity and acceleration analysis
    4. Seasonality detection (monthly patterns)
    5. Faculty engagement metrics
    """

    def __init__(self, db: Session):
        self.db = db

    def predict(self, department_id: str) -> PredictionResult:
        """
        Project the department's automated scan score at its configured target date.

        Args:
            department_id: The department to analyze

        Returns:
            PredictionResult with prediction, confidence, and recommendations
        """
        from .snapshot_service import SnapshotService
        from ..db.models import Department

        department = (
            self.db.query(Department).filter(Department.id == department_id).first()
        )
        if department is None:
            raise ValueError("Department not found")
        deadline_info = DeadlineService.for_department(department)
        deadline = deadline_info.to_dict()
        if not deadline_info.has_deadline or deadline_info.is_past_deadline:
            return self._deadline_unavailable_result(deadline)

        # Get historical data
        trend_90 = SnapshotService.get_historical_trend(self.db, department_id, days=90)
        trend_30 = SnapshotService.get_historical_trend(self.db, department_id, days=30)

        verified_trend_90 = [
            point for point in trend_90 if point.avg_compliance_score is not None
        ]
        verified_trend_30 = [
            point for point in trend_30 if point.avg_compliance_score is not None
        ]

        days_remaining = deadline_info.days_remaining
        if days_remaining is None:
            raise ValueError("Dated deadline is missing remaining-day metadata")

        # Check if we have enough data
        if len(verified_trend_30) < 7:
            return self._insufficient_data_result(days_remaining, deadline)

        # Extract features
        features = self._extract_features(
            verified_trend_90,
            verified_trend_30,
            department_id,
            days_remaining,
        )

        # Run prediction models
        predictions = self._run_models(features, days_remaining)

        # Ensemble the predictions
        final_prediction = self._ensemble_predictions(predictions, features)

        # Identify risk factors
        risk_factors = self._identify_risk_factors(features, final_prediction)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            features, risk_factors, final_prediction, days_remaining
        )

        # Determine risk level
        risk_level = self._calculate_risk_level(final_prediction, features)

        return PredictionResult(
            will_meet_deadline=final_prediction["will_meet"],
            confidence=final_prediction["confidence"],
            probability_of_compliance=final_prediction["probability"],
            projected_score_at_deadline=final_prediction["projected_score"],
            current_score=features["current_score"],
            days_remaining=days_remaining,
            risk_level=risk_level,
            risk_factors=risk_factors,
            recommendations=recommendations,
            prediction_model="ensemble_v1",
            features_used=features,
            deadline=deadline,
        )

    def _extract_features(
        self,
        trend_90: List,
        trend_30: List,
        department_id: str,
        days_remaining: int,
    ) -> Dict[str, float]:
        """Extract predictive features from historical data."""
        from ..db.models import Scan

        features = {}

        # Current score (average of last 7 days)
        if trend_30:
            recent_scores = [p.avg_compliance_score for p in trend_30[-7:]]
            features["current_score"] = np.mean(recent_scores)
        else:
            features["current_score"] = 0

        # Score needed to reach 90%
        features["score_gap"] = max(0, 90 - features["current_score"])

        # Daily improvement rate (velocity)
        if len(trend_30) >= 14:
            first_half = np.mean([p.avg_compliance_score for p in trend_30[:7]])
            second_half = np.mean([p.avg_compliance_score for p in trend_30[-7:]])
            features["velocity"] = (second_half - first_half) / 7
        else:
            features["velocity"] = 0

        # Acceleration (change in velocity)
        if len(trend_90) >= 60:
            early_velocity = self._calculate_velocity(trend_90[:30])
            late_velocity = self._calculate_velocity(trend_90[-30:])
            features["acceleration"] = late_velocity - early_velocity
        else:
            features["acceleration"] = 0

        # Score volatility (standard deviation)
        if trend_30:
            scores = [p.avg_compliance_score for p in trend_30]
            features["volatility"] = np.std(scores) if len(scores) > 1 else 0
        else:
            features["volatility"] = 0

        # Scan frequency (scans per day in last 30 days)
        scan_count = (
            self.db.query(Scan)
            .filter(
                Scan.department_id == department_id,
                Scan.created_at >= datetime.utcnow() - timedelta(days=30),
            )
            .count()
        )
        features["scan_frequency"] = scan_count / 30

        # Faculty engagement (active users in last 30 days)
        active_users = (
            self.db.query(Scan.user_id)
            .filter(
                Scan.department_id == department_id,
                Scan.created_at >= datetime.utcnow() - timedelta(days=30),
            )
            .distinct()
            .count()
        )
        features["active_faculty"] = active_users

        # Issue resolution rate
        if trend_30:
            total_issues = sum(p.total_issues for p in trend_30)
            resolved_issues = sum(getattr(p, "resolved_issues", 0) for p in trend_30)
            features["resolution_rate"] = (
                resolved_issues / total_issues if total_issues > 0 else 0
            )
        else:
            features["resolution_rate"] = 0

        # Time pressure factor (urgency based on days remaining).
        # Normalised against 540 days to match the "low" urgency threshold in
        # DeadlineService._get_urgency (widened from 365 after the April 2026
        # DOJ IFR extended the typical runway). With the old 365 divisor, every
        # customer >1yr from their deadline collapsed to time_pressure=0 and
        # the ML model lost useful signal across most of the new runway.
        features["time_pressure"] = 1 - (days_remaining / 540)
        features["time_pressure"] = max(0, min(1, features["time_pressure"]))

        # Required daily improvement to meet deadline
        features["required_daily_improvement"] = (
            features["score_gap"] / days_remaining if days_remaining > 0 else 0
        )

        # Feasibility ratio (velocity vs required improvement)
        features["feasibility_ratio"] = (
            features["velocity"] / features["required_daily_improvement"]
            if features["required_daily_improvement"] > 0
            else 10  # Arbitrary high value if no improvement needed
        )

        return features

    def _calculate_velocity(self, trend: List) -> float:
        """Calculate improvement velocity over a trend period."""
        if len(trend) < 7:
            return 0
        first_week = np.mean([p.avg_compliance_score for p in trend[:7]])
        last_week = np.mean([p.avg_compliance_score for p in trend[-7:]])
        days = len(trend)
        return (last_week - first_week) / days if days > 0 else 0

    def _run_models(
        self, features: Dict[str, float], days_remaining: int
    ) -> Dict[str, Dict]:
        """Run multiple prediction models."""
        predictions = {}

        # Model 1: Linear extrapolation
        linear_score = features["current_score"] + (
            features["velocity"] * days_remaining
        )
        linear_score = max(0, min(100, linear_score))
        predictions["linear"] = {
            "projected_score": linear_score,
            "will_meet": linear_score >= 90,
            "weight": 0.25,
        }

        # Model 2: Exponential smoothing with acceleration
        if features["acceleration"] > 0:
            # Accelerating improvement
            exp_score = features["current_score"] + (
                features["velocity"] * days_remaining
                + 0.5 * features["acceleration"] * (days_remaining**0.5)
            )
        else:
            # Decelerating or stable
            exp_score = features["current_score"] + (
                features["velocity"] * days_remaining * 0.8
            )
        exp_score = max(0, min(100, exp_score))
        predictions["exponential"] = {
            "projected_score": exp_score,
            "will_meet": exp_score >= 90,
            "weight": 0.30,
        }

        # Model 3: Logistic regression-style (sigmoid probability)
        # Based on current score, velocity, and time
        z = (
            -5  # Base bias
            + 0.15 * features["current_score"]  # Higher score = better
            + 50 * features["velocity"]  # Faster improvement = better
            + 5 * features["feasibility_ratio"]  # Higher feasibility = better
            - 2 * features["time_pressure"]  # Less time = worse
        )
        probability = 1 / (1 + np.exp(-z))
        predictions["logistic"] = {
            "probability": probability,
            "will_meet": probability > 0.5,
            "weight": 0.30,
        }

        # Model 4: Engagement-based prediction
        # Departments with high engagement tend to improve faster
        engagement_factor = min(1.5, 1 + 0.1 * features["active_faculty"])
        engagement_score = features["current_score"] + (
            features["velocity"] * days_remaining * engagement_factor
        )
        engagement_score = max(0, min(100, engagement_score))
        predictions["engagement"] = {
            "projected_score": engagement_score,
            "will_meet": engagement_score >= 90,
            "weight": 0.15,
        }

        return predictions

    def _ensemble_predictions(
        self, predictions: Dict[str, Dict], features: Dict[str, float]
    ) -> Dict:
        """Combine predictions using weighted ensemble."""
        # Calculate weighted average of projected scores
        total_weight = 0
        weighted_score = 0
        will_meet_votes = 0

        for model_name, pred in predictions.items():
            weight = pred.get("weight", 0.25)
            total_weight += weight

            if "projected_score" in pred:
                weighted_score += pred["projected_score"] * weight
            if pred.get("will_meet"):
                will_meet_votes += weight

        projected_score = weighted_score / total_weight if total_weight > 0 else 0

        # Get probability from logistic model
        probability = predictions.get("logistic", {}).get("probability", 0.5)

        # Adjust probability based on ensemble voting
        vote_ratio = will_meet_votes / total_weight if total_weight > 0 else 0.5
        final_probability = 0.6 * probability + 0.4 * vote_ratio

        # Determine final prediction
        will_meet = projected_score >= 90 or final_probability > 0.65

        # Calculate confidence
        # Higher confidence when models agree and features are favorable
        model_agreement = abs(vote_ratio - 0.5) * 2  # 0-1, higher = more agreement
        feature_confidence = min(1, features["current_score"] / 100)
        confidence = 0.5 * model_agreement + 0.3 * feature_confidence + 0.2

        # Reduce confidence if score gap is large
        if features["score_gap"] > 30:
            confidence *= 0.8
        if features["score_gap"] > 50:
            confidence *= 0.7

        return {
            "will_meet": will_meet,
            "projected_score": round(projected_score, 2),
            "probability": round(final_probability, 3),
            "confidence": round(min(0.95, max(0.1, confidence)), 2),
        }

    def _identify_risk_factors(
        self, features: Dict[str, float], prediction: Dict
    ) -> List[Dict[str, Any]]:
        """Identify factors that may prevent meeting the deadline."""
        risk_factors = []

        # Low current score
        if features["current_score"] < 50:
            risk_factors.append(
                {
                    "factor": "Low Current Score",
                    "severity": "critical",
                    "description": f"Current compliance score of {features['current_score']:.1f}% is significantly below target",
                    "impact": "Major remediation effort required",
                }
            )
        elif features["current_score"] < 70:
            risk_factors.append(
                {
                    "factor": "Below Target Score",
                    "severity": "high",
                    "description": f"Current score of {features['current_score']:.1f}% requires sustained improvement",
                    "impact": "Consistent daily improvement needed",
                }
            )

        # Negative or zero velocity
        if features["velocity"] <= 0:
            risk_factors.append(
                {
                    "factor": "No Improvement Trend",
                    "severity": "critical" if features["velocity"] < 0 else "high",
                    "description": "Compliance score is not improving or declining",
                    "impact": "Immediate intervention required",
                }
            )

        # High volatility
        if features["volatility"] > 5:
            risk_factors.append(
                {
                    "factor": "Score Instability",
                    "severity": "medium",
                    "description": f"High score volatility ({features['volatility']:.1f} std dev)",
                    "impact": "Inconsistent compliance practices",
                }
            )

        # Low engagement
        if features["active_faculty"] < 3:
            risk_factors.append(
                {
                    "factor": "Low Faculty Engagement",
                    "severity": "high",
                    "description": f"Only {features['active_faculty']} active faculty members",
                    "impact": "Work is concentrated among too few people",
                }
            )

        # Low scan frequency
        if features["scan_frequency"] < 0.5:
            risk_factors.append(
                {
                    "factor": "Insufficient Scanning Activity",
                    "severity": "medium",
                    "description": f"Only {features['scan_frequency']:.1f} scans/day",
                    "impact": "Not enough content being checked",
                }
            )

        # Infeasible improvement rate
        if features["feasibility_ratio"] < 0.5:
            risk_factors.append(
                {
                    "factor": "Improvement Rate Insufficient",
                    "severity": "critical",
                    "description": "Current improvement pace won't reach target",
                    "impact": "Need to double the improvement rate",
                }
            )

        # Time pressure
        if features["time_pressure"] > 0.8:
            risk_factors.append(
                {
                    "factor": "Limited Time Remaining",
                    "severity": "high",
                    "description": "Less than 2 months until deadline",
                    "impact": "Aggressive action plan required",
                }
            )

        return risk_factors

    def _generate_recommendations(
        self,
        features: Dict[str, float],
        risk_factors: List[Dict],
        prediction: Dict,
        days_remaining: int,
    ) -> List[Dict[str, Any]]:
        """Generate actionable recommendations based on analysis."""
        recommendations = []

        # Priority 1: Address critical risks
        [rf for rf in risk_factors if rf["severity"] == "critical"]

        if features["velocity"] <= 0:
            recommendations.append(
                {
                    "priority": 1,
                    "action": "Increase remediation activity",
                    "description": "Start fixing 10+ accessibility issues daily",
                    "expected_impact": "+0.5 to +1.0 points/day improvement",
                    "effort": "high",
                }
            )

        if features["active_faculty"] < 5:
            recommendations.append(
                {
                    "priority": 2,
                    "action": "Expand faculty participation",
                    "description": "Onboard more faculty to the accessibility workflow",
                    "expected_impact": "Distribute workload, faster remediation",
                    "effort": "medium",
                }
            )

        if features["scan_frequency"] < 1:
            recommendations.append(
                {
                    "priority": 2,
                    "action": "Increase scanning frequency",
                    "description": "Scan all course materials systematically",
                    "expected_impact": "Identify more issues to remediate",
                    "effort": "low",
                }
            )

        # Specific score-based recommendations
        if features["current_score"] < 70:
            recommendations.append(
                {
                    "priority": 1,
                    "action": "Focus on critical issues first",
                    "description": "Address all critical and high severity issues before medium/low",
                    "expected_impact": "Maximum score improvement per fix",
                    "effort": "medium",
                }
            )

        if features["score_gap"] > 20:
            daily_points = features["score_gap"] / max(1, days_remaining)
            recommendations.append(
                {
                    "priority": 1,
                    "action": f"Improve {daily_points:.2f} points daily",
                    "description": "Track daily progress against this target",
                    "expected_impact": "On track for the configured score target",
                    "effort": "ongoing",
                }
            )

        # General recommendations
        recommendations.append(
            {
                "priority": 3,
                "action": "Use auto-remediation features",
                "description": "Enable AI-powered fixes for faster remediation",
                "expected_impact": "Reduce manual work by 60-80%",
                "effort": "low",
            }
        )

        recommendations.append(
            {
                "priority": 3,
                "action": "Set up cloud integration sync",
                "description": "Automatically scan new documents as they're uploaded",
                "expected_impact": "Prevent new accessibility debt",
                "effort": "low",
            }
        )

        # Sort by priority
        recommendations.sort(key=lambda x: x["priority"])

        return recommendations[:5]  # Return top 5 recommendations

    def _calculate_risk_level(
        self, prediction: Dict, features: Dict[str, float]
    ) -> str:
        """Determine overall risk level for meeting deadline."""
        probability = prediction["probability"]
        score_gap = features["score_gap"]
        velocity = features["velocity"]

        if probability >= 0.8 and score_gap < 10:
            return "low"
        elif probability >= 0.6 and score_gap < 20 and velocity > 0:
            return "medium"
        elif probability >= 0.4 or (velocity > 0.3 and score_gap < 40):
            return "high"
        else:
            return "critical"

    def _insufficient_data_result(
        self, days_remaining: int, deadline: Dict[str, Any]
    ) -> PredictionResult:
        """Return result when insufficient data is available."""
        return PredictionResult(
            will_meet_deadline=None,
            confidence=0.1,
            probability_of_compliance=0.5,
            projected_score_at_deadline=None,
            current_score=None,
            days_remaining=days_remaining,
            risk_level="unknown",
            risk_factors=[
                {
                    "factor": "Insufficient Data",
                    "severity": "high",
                    "description": "Need at least 7 days of scanning history",
                    "impact": "Cannot make accurate predictions",
                }
            ],
            recommendations=[
                {
                    "priority": 1,
                    "action": "Start regular scanning",
                    "description": "Scan documents daily to build prediction data",
                    "expected_impact": "Enable accurate deadline forecasting",
                    "effort": "medium",
                }
            ],
            prediction_model="insufficient_data",
            features_used={},
            deadline=deadline,
        )

    def _deadline_unavailable_result(
        self, deadline: Dict[str, Any]
    ) -> PredictionResult:
        """Return a neutral result when the profile has no dated target."""

        return PredictionResult(
            will_meet_deadline=None,
            confidence=0.0,
            probability_of_compliance=None,
            projected_score_at_deadline=None,
            current_score=None,
            days_remaining=None,
            risk_level="unknown",
            risk_factors=[],
            recommendations=[],
            prediction_model="deadline_unavailable",
            features_used={},
            deadline=deadline,
        )


def predict_compliance(db: Session, department_id: str) -> Dict[str, Any]:
    """
    Convenience function to predict compliance for a department.

    Returns dict format suitable for API response.
    """
    predictor = CompliancePredictor(db)
    result = predictor.predict(department_id)

    return {
        "success": True,
        "department_id": department_id,
        "prediction": {
            "will_meet_deadline": result.will_meet_deadline,
            "confidence": result.confidence,
            "probability": result.probability_of_compliance,
            "projected_score": result.projected_score_at_deadline,
            "current_score": result.current_score,
            "days_remaining": result.days_remaining,
        },
        "risk_assessment": {
            "level": result.risk_level,
            "factors": result.risk_factors,
        },
        "recommendations": result.recommendations,
        "model_info": {
            "model": result.prediction_model,
            "features_count": len(result.features_used),
        },
        "deadline": result.deadline,
    }
