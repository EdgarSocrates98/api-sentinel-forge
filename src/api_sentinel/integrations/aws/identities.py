"""STS preflight — identity verified before any collection (§16.3).

`GetCallerIdentity` confirms the resolved credential chain's account;
an explicit expected account/region in the source config is compared
and a mismatch is a named refusal, never a proceed-anyway. Receipts
carry the account in masked + hashed form — the full ARN is never
persisted.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from api_sentinel.integrations.aws.budgets import aws_error_code
from api_sentinel.integrations.aws.client import ReadOnlyClient
from api_sentinel.integrations.aws.errors import aws_refused, aws_unavailable

_ACCOUNT_RE = re.compile(r"^\d{12}$")


def mask_account_id(account: str) -> str:
    """Mask the middle of a 12-digit account id: 123456789012 → 1234****9012."""
    if len(account) >= 8:
        return f"{account[:4]}****{account[-4:]}"
    return "****"


def account_hash(account: str) -> str:
    """Stable pseudonymous account handle for receipts/audit (sha256:16)."""
    digest = hashlib.sha256(f"aws-account\0{account}".encode()).hexdigest()
    return f"sha256:{digest[:16]}"


def mask_arn(arn: str, account: str) -> str:
    """Replace the embedded account id inside an ARN with its masked form."""
    return arn.replace(account, mask_account_id(account))


@dataclass(frozen=True)
class AwsIdentity:
    """Verified caller identity — account kept in raw form only in memory."""

    account_id: str
    arn: str
    user_id: str
    region: str

    @property
    def masked_account(self) -> str:
        return mask_account_id(self.account_id)

    @property
    def account_digest(self) -> str:
        return account_hash(self.account_id)

    @property
    def masked_arn(self) -> str:
        return mask_arn(self.arn, self.account_id)


def verify_identity(
    clients_sts: ReadOnlyClient,
    *,
    region: str,
    expected_account_id: str | None = None,
    expected_region: str | None = None,
) -> AwsIdentity:
    """Run the STS preflight; returns the verified identity or a named refusal.

    Account/region mismatch is refused *before* any metric call — the
    cycle never collects against the wrong scope.
    """
    try:
        response = clients_sts.get_caller_identity()
    except Exception as exc:
        code = aws_error_code(exc)
        if code is not None:
            raise aws_refused(
                f"sts:GetCallerIdentity denied ({code}) — identity must be "
                "verifiable before collection",
                details={"aws_error": code},
            ) from exc
        raise aws_unavailable(
            f"sts:GetCallerIdentity failed: {type(exc).__name__}: {exc}",
        ) from exc
    account = str(response.get("Account") or "")
    if not _ACCOUNT_RE.match(account):
        raise aws_unavailable(
            "sts:GetCallerIdentity returned an unusable account id",
            details={"account": "missing-or-malformed"},
        )
    if expected_account_id and account != expected_account_id:
        raise aws_refused(
            "account mismatch: expected "
            f"{mask_account_id(expected_account_id)}, caller is "
            f"{mask_account_id(account)} — wrong credential scope",
            details={
                "expected": mask_account_id(expected_account_id),
                "caller": mask_account_id(account),
            },
        )
    if expected_region and region != expected_region:
        raise aws_refused(
            f"region mismatch: expected {expected_region}, session resolved "
            f"{region} — explicit scope required",
            details={"expected": expected_region, "resolved": region},
        )
    return AwsIdentity(
        account_id=account,
        arn=str(response.get("Arn") or ""),
        user_id=str(response.get("UserId") or ""),
        region=region,
    )
