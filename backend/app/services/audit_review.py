"""审计复核样本领域服务：把变更事件的事务镜像转换成按查阅权限脱敏的字段级证据。

上游：change_event 提供带事务ID的变更前后镜像
下游：audit_trail 复核台据此生成样本、确认字段、执行补证回放和整笔事务证明

关键约束：
1. 生成样本和回查旧样本都会用当前授权重新判定字段可见性；
2. 越权字段不返回字段值，也不计入已确认范围；
3. 只有事务内所有成员、所有实际变更字段都取得仍然有效的确认，才能出具整笔证明。
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime
from threading import RLock
from typing import Any

from app.pipeline import pipeline_bus
from app.services.change_event import change_event_service as _change_events
from app.store import store

# 示例账号：订单状态复核员只有订单只读权限，且只能看到 status 字段。
_ACCOUNT_SEED: list[dict[str, Any]] = [
    {
        "account_id": "order_status_reader",
        "name": "订单状态复核员",
        "description": "当前账号对订单只读，仅能查阅 sales.orders.status 字段",
        "read_only": True,
        "grants": [
            {"table": "sales.orders", "fields": ["status"], "active": True},
        ],
    },
    {
        "account_id": "customer_team_reader",
        "name": "客户团队复核员",
        "description": "客户团队账号，仅能查阅本团队归属的客户属性",
        "read_only": True,
        "grants": [
            {"table": "sales.customers", "fields": ["*"], "active": True},
        ],
    },
    {
        "account_id": "data_steward",
        "name": "数据治理复核员",
        "description": "数据治理组账号，可复核订单与客户两侧字段",
        "read_only": True,
        "grants": [
            {"table": "sales.orders", "fields": ["*"], "active": True},
            {"table": "sales.customers", "fields": ["*"], "active": True},
        ],
    },
]


class AuditReviewError(Exception):
    """复核服务的可预期业务错误，路由层会转换成对应的 HTTP 状态。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_table(value: Any) -> str:
    return str(value or "").strip().lower()


class AuditReviewService:
    """内存版复核样本服务；示例项目里用单例保持请求间的复核状态。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._accounts: dict[str, dict[str, Any]] = {
            item["account_id"]: copy.deepcopy(item) for item in _ACCOUNT_SEED
        }
        self._samples: dict[str, dict[str, Any]] = {}
        self._confirmations: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # 账号与授权
    # ------------------------------------------------------------------
    def _require_account(self, account_id: str) -> dict[str, Any]:
        account = self._accounts.get(str(account_id or ""))
        if account is None:
            raise AuditReviewError(f"复核账号 {account_id or '(空)'} 不存在", 404)
        return account

    def _public_account(self, account: dict[str, Any]) -> dict[str, Any]:
        return {
            "account_id": account["account_id"],
            "name": account["name"],
            "description": account["description"],
            "read_only": account["read_only"],
            "grants": [
                {
                    "table": grant["table"],
                    "fields": list(grant.get("fields", [])),
                    "denied_fields": list(grant.get("denied_fields", [])),
                    "active": bool(grant.get("active")),
                }
                for grant in account.get("grants", [])
            ],
        }

    def list_accounts(self) -> dict[str, Any]:
        with self._lock:
            items = [
                self._public_account(account)
                for account in sorted(self._accounts.values(), key=lambda item: item["account_id"])
            ]
        return {"items": items, "total": len(items)}

    def _active_grant(self, account_id: str, table: str) -> dict[str, Any] | None:
        account = self._accounts.get(str(account_id or ""))
        if account is None:
            return None
        wanted = _normalize_table(table)
        for grant in account.get("grants", []):
            if not grant.get("active"):
                continue
            granted = _normalize_table(grant.get("table"))
            if granted == wanted or wanted.endswith(f".{granted}") or granted.endswith(f".{wanted}"):
                return grant
        return None

    def _field_allowed(self, account_id: str, table: str, field: str) -> bool:
        if not account_id or not field:
            return False
        grant = self._active_grant(account_id, table)
        if grant is None:
            return False
        if str(field) in [str(item) for item in grant.get("denied_fields", [])]:
            return False
        fields = [str(item) for item in grant.get("fields", [])]
        return "*" in fields or str(field) in fields

    def revoke_grant(self, account_id: str, table: str, field: str | None = None) -> dict[str, Any]:
        """收回账号的整表或单字段查阅权限；历史确认记录保留但不再有效。"""
        account = self._require_account(account_id)
        wanted = _normalize_table(table)
        changed = False
        with self._lock:
            for grant in account.get("grants", []):
                granted = _normalize_table(grant.get("table"))
                if not (
                    granted == wanted
                    or wanted.endswith(f".{granted}")
                    or granted.endswith(f".{wanted}")
                ):
                    continue
                if field:
                    wildcard = "*" in [str(item) for item in grant.get("fields", [])]
                    if str(field) not in [str(item) for item in grant.get("denied_fields", [])]:
                        grant.setdefault("denied_fields", []).append(str(field))
                    if not wildcard:
                        # 字段白名单直接移除；通配授权保留 * 并用拒绝清单表达收回。
                        grant["fields"] = [
                            item for item in grant.get("fields", []) if str(item) != str(field)
                        ]
                        if not grant["fields"]:
                            grant["active"] = False
                else:
                    grant["active"] = False
                changed = True
                break
        if not changed:
            raise AuditReviewError(f"账号 {account_id} 没有可收回的 {table} 授权", 404)
        target = f"{table}.{field}" if field else table
        pipeline_bus.write_audit(
            "权限收回", target,
            f"账号 {account_id} 的 {target} 查阅权限已收回，历史确认需重新判定",
            "audit_trail", "已记录",
        )
        return self._public_account(account)

    def grant_access(self, account_id: str, table: str, fields: list[str]) -> dict[str, Any]:
        """重新授予字段权限，用于复核演练后的恢复。"""
        account = self._require_account(account_id)
        clean_fields = [str(item) for item in (fields or []) if str(item).strip()]
        if not clean_fields:
            raise AuditReviewError("授权字段不能为空", 400)
        wanted = _normalize_table(table)
        with self._lock:
            for grant in account.get("grants", []):
                granted = _normalize_table(grant.get("table"))
                if (
                    granted == wanted
                    or wanted.endswith(f".{granted}")
                    or granted.endswith(f".{wanted}")
                ):
                    grant["table"] = table
                    grant["fields"] = clean_fields
                    grant["denied_fields"] = []
                    grant["active"] = True
                    break
            else:
                account.setdefault("grants", []).append(
                    {"table": table, "fields": clean_fields, "active": True}
                )
        pipeline_bus.write_audit(
            "权限授予", table,
            f"账号 {account_id} 获得 {table} 字段权限：{', '.join(clean_fields)}",
            "audit_trail", "已记录",
        )
        return self._public_account(account)

    # ------------------------------------------------------------------
    # 事务与字段级证据
    # ------------------------------------------------------------------
    def _members_for_tx(self, tx_id: str) -> list[dict[str, Any]]:
        """向 change_event 模块索取该事务的全部镜像成员。"""
        members = _change_events.transaction_members(tx_id)
        if not members:
            raise AuditReviewError(f"事务 {tx_id} 没有可复核的字段级变更证据", 404)
        return members

    def list_transactions(self) -> dict[str, Any]:
        tx_ids: list[str] = []
        with self._lock:
            for row in store.rows("change_event"):
                tx_id = str(row.get("事务ID") or "").strip()
                if tx_id and tx_id not in tx_ids:
                    tx_ids.append(tx_id)
        items = []
        for tx_id in tx_ids:
            # 成员与字段口径统一由变更事件服务给出，避免两处解析规则漂移。
            members = _change_events.transaction_members(tx_id)
            if not members:
                continue
            state = self._attestation_state(tx_id)
            with self._lock:
                sample_count = sum(
                    1 for sample in self._samples.values() if sample["tx_id"] == tx_id
                )
            items.append({
                "tx_id": tx_id,
                "member_count": len(members),
                "tables": sorted({member["table"] for member in members}),
                "changed_field_count": sum(
                    len(member["changed_fields"]) for member in members
                ),
                "event_time": max(
                    (member["event_time"] for member in members), default=""
                ),
                "sample_count": sample_count,
                "attested": state["attested"],
            })
        items.sort(key=lambda item: (item["event_time"], item["tx_id"]), reverse=True)
        return {"items": items, "total": len(items)}

    # ------------------------------------------------------------------
    # 样本生成与回查
    # ------------------------------------------------------------------
    def generate_sample(self, tx_id: str, account_id: str) -> dict[str, Any]:
        """按当前账号权限生成脱敏复核样本集，原始镜像只保存在服务端。"""
        account = self._require_account(account_id)
        members = self._members_for_tx(tx_id)
        sample = {
            "sample_id": f"smp_{uuid.uuid4().hex[:12]}",
            "tx_id": str(tx_id),
            "account_id": account["account_id"],
            "generated_at": _now(),
            "members": copy.deepcopy(members),
        }
        with self._lock:
            self._samples[sample["sample_id"]] = sample
        rendered = self._render_sample(sample, account["account_id"])
        pipeline_bus.write_audit(
            "样本生成", str(tx_id),
            (
                f"账号 {account['account_id']} 生成样本 {sample['sample_id']}，"
                f"可见 {rendered['visible_field_count']} 个字段，"
                f"脱敏 {rendered['masked_field_count']} 个字段"
            ),
            "audit_trail", "已记录",
        )
        return rendered

    def list_samples(self, account_id: str, tx_id: str | None = None) -> dict[str, Any]:
        self._require_account(account_id)
        with self._lock:
            samples = [
                sample for sample in self._samples.values()
                if not tx_id or sample["tx_id"] == str(tx_id)
            ]
            items = [
                {
                    "sample_id": sample["sample_id"],
                    "tx_id": sample["tx_id"],
                    "account_id": sample["account_id"],
                    "generated_at": sample["generated_at"],
                    "member_count": len(sample["members"]),
                }
                for sample in sorted(
                    samples, key=lambda item: item["generated_at"], reverse=True
                )
            ]
        return {"items": items, "total": len(items)}

    def get_sample(self, sample_id: str, account_id: str) -> dict[str, Any]:
        """回查旧样本时重新按账号当前权限脱敏，不沿用生成时的可见范围。"""
        self._require_account(account_id)
        with self._lock:
            sample = self._samples.get(str(sample_id))
        if sample is None:
            raise AuditReviewError(f"复核样本 {sample_id} 不存在", 404)
        rendered = self._render_sample(sample, str(account_id))
        pipeline_bus.write_audit(
            "样本回查", str(sample["tx_id"]),
            (
                f"账号 {account_id} 回查样本 {sample_id}，"
                f"按当前权限可见 {rendered['visible_field_count']} 个字段，"
                f"脱敏 {rendered['masked_field_count']} 个字段"
            ),
            "audit_trail", "已记录",
        )
        return rendered

    def _render_member(
        self,
        member: dict[str, Any],
        account_id: str,
        confirmations: dict[tuple[str, str], dict[str, Any]],
        global_confirmations: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        visible_fields: list[dict[str, Any]] = []
        masked_count = 0
        confirmed_fields: list[str] = []
        missing_fields: list[str] = []
        for field in member["changed_fields"]:
            if not self._field_allowed(account_id, member["table"], field):
                masked_count += 1
                continue
            visible_fields.append({
                "field": field,
                "before": member["before"].get(field),
                "after": member["after"].get(field),
                "changed": True,
            })
            if (member["member_id"], field) in confirmations:
                confirmed_fields.append(field)
            else:
                missing_fields.append(field)
        # 是否仍需补证按整笔事务的有效确认判定，不因当前账号看不到而重复计入。
        global_missing = [
            field for field in member["changed_fields"]
            if (member["member_id"], field) not in global_confirmations
        ]
        needs_evidence = bool(global_missing)
        if needs_evidence and masked_count:
            reason = "该成员仍有未取得有效复核的字段，其中部分字段超出当前账号查阅范围"
        elif needs_evidence:
            reason = "该成员仍有未取得有效复核的字段"
        elif masked_count:
            reason = "该成员已取得有效复核，但部分字段超出当前账号查阅范围，不予显示"
        else:
            reason = "该成员已完整复核"
        return {
            "member_id": member["member_id"],
            "event_id": member["event_id"],
            "table": member["table"],
            "operation": member["operation"],
            "event_time": member["event_time"],
            "fields": visible_fields,
            "visible_field_count": len(visible_fields),
            "masked_field_count": masked_count,
            "required_field_count": len(member["changed_fields"]),
            "confirmed_fields": confirmed_fields,
            "missing_fields": missing_fields,
            "global_missing_field_count": len(global_missing),
            "needs_evidence": needs_evidence,
            "reason": reason,
        }

    def _confirmed_scope(self, tx_id: str, account_id: str) -> list[dict[str, Any]]:
        confirmations = self._effective_confirmations(tx_id, requester=account_id)
        scope: list[dict[str, Any]] = []
        for confirmation in confirmations.values():
            scope.append({
                "member_id": confirmation["member_id"],
                "table": confirmation["table"],
                "field": confirmation["field"],
                "before": confirmation["before"],
                "after": confirmation["after"],
                "decision": confirmation["decision"],
                "confirmed_by": confirmation["confirmed_by"],
                "confirmed_at": confirmation["confirmed_at"],
            })
        scope.sort(key=lambda item: (item["table"], item["field"], item["member_id"]))
        return scope

    def _render_sample(self, sample: dict[str, Any], account_id: str) -> dict[str, Any]:
        confirmations = self._effective_confirmations(sample["tx_id"], requester=account_id)
        global_confirmations = self._effective_confirmations(sample["tx_id"])
        members = [
            self._render_member(member, account_id, confirmations, global_confirmations)
            for member in sample["members"]
        ]
        scope = self._confirmed_scope(sample["tx_id"], account_id)
        state = self._attestation_state(sample["tx_id"])
        return {
            "sample_id": sample["sample_id"],
            "tx_id": sample["tx_id"],
            "account_id": account_id,
            "generated_by": sample["account_id"],
            "generated_at": sample["generated_at"],
            "members": members,
            "member_count": len(members),
            "visible_field_count": sum(item["visible_field_count"] for item in members),
            "masked_field_count": sum(item["masked_field_count"] for item in members),
            "confirmed_scope": scope,
            "confirmed_field_count": len(scope),
            "members_to_supplement": [
                item["member_id"] for item in members if item["needs_evidence"]
            ],
            "attested": state["attested"],
            "attestation": {
                "attested": state["attested"],
                "reason": state["reason"],
            },
            "notice": "仅展示当前账号有权查阅的字段；越权字段已脱敏，且不计入已确认范围。",
        }

    # ------------------------------------------------------------------
    # 字段复核与确认记录
    # ------------------------------------------------------------------
    def confirm_field(
        self,
        sample_id: str,
        account_id: str,
        member_id: str,
        field: str,
        decision: str = "确认一致",
        remark: str = "",
    ) -> dict[str, Any]:
        """从样本生成一条按字段划分的确认记录；确认前重新判定字段权限。"""
        account = self._require_account(account_id)
        with self._lock:
            sample = self._samples.get(str(sample_id))
        if sample is None:
            raise AuditReviewError(f"复核样本 {sample_id} 不存在", 404)
        member = next(
            (item for item in sample["members"] if item["member_id"] == str(member_id)),
            None,
        )
        if member is None:
            raise AuditReviewError(f"样本 {sample_id} 中不存在成员 {member_id}", 404)
        field = str(field or "")
        if field not in member["changed_fields"]:
            raise AuditReviewError(f"字段 {field or '(空)'} 不是该成员的实际变更字段", 400)
        if not self._field_allowed(account["account_id"], member["table"], field):
            # 不返回字段值，也不允许把越权字段计入确认范围。
            raise AuditReviewError(
                f"字段 {field} 超出账号 {account['account_id']} 的查阅权限，不能复核",
                403,
            )
        decision = str(decision or "确认一致").strip()
        if not decision:
            decision = "确认一致"
        with self._lock:
            confirmation = next(
                (
                    item for item in self._confirmations
                    if item["sample_id"] == str(sample_id)
                    and item["member_id"] == str(member_id)
                    and item["field"] == field
                    and item["confirmed_by"] == account["account_id"]
                ),
                None,
            )
            if confirmation is None:
                confirmation = {
                    "confirmation_id": f"cfm_{uuid.uuid4().hex[:12]}",
                    "sample_id": str(sample_id),
                    "tx_id": sample["tx_id"],
                    "member_id": str(member_id),
                    "table": member["table"],
                    "field": field,
                    "before": member["before"].get(field),
                    "after": member["after"].get(field),
                    "decision": decision,
                    "remark": remark,
                    "confirmed_by": account["account_id"],
                    "confirmed_at": _now(),
                }
                self._confirmations.append(confirmation)
            else:
                confirmation["decision"] = decision
                confirmation["remark"] = remark
                confirmation["before"] = member["before"].get(field)
                confirmation["after"] = member["after"].get(field)
                confirmation["confirmed_at"] = _now()
        pipeline_bus.write_audit(
            "字段复核", f"{member['table']}.{field}",
            (
                f"账号 {account['account_id']} 基于样本 {sample_id} 确认事务 "
                f"{sample['tx_id']} 成员 {member_id} 的 {field} 字段：{decision}"
            ),
            "audit_trail", "已复核",
        )
        rendered = self._render_sample(sample, account["account_id"])
        return {
            "ok": True,
            "message": f"字段 {field} 已生成复核确认记录",
            "confirmation": self._public_confirmation(confirmation, True),
            "sample": rendered,
        }

    def _public_confirmation(
        self, confirmation: dict[str, Any], effective: bool
    ) -> dict[str, Any]:
        return {
            "confirmation_id": confirmation["confirmation_id"],
            "sample_id": confirmation["sample_id"],
            "tx_id": confirmation["tx_id"],
            "member_id": confirmation["member_id"],
            "table": confirmation["table"],
            "field": confirmation["field"],
            "before": confirmation["before"],
            "after": confirmation["after"],
            "decision": confirmation["decision"],
            "remark": confirmation.get("remark", ""),
            "confirmed_by": confirmation["confirmed_by"],
            "confirmed_at": confirmation["confirmed_at"],
            "effective": effective,
        }

    def _effective_confirmations(
        self, tx_id: str, requester: str | None = None
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """只保留确认人与查阅人当前都仍有字段权限的确认记录。"""
        latest: dict[tuple[str, str], dict[str, Any]] = {}
        with self._lock:
            confirmations = list(self._confirmations)
        for confirmation in confirmations:
            if confirmation["tx_id"] != str(tx_id):
                continue
            if not self._field_allowed(
                confirmation["confirmed_by"], confirmation["table"], confirmation["field"]
            ):
                continue
            if requester is not None and not self._field_allowed(
                requester, confirmation["table"], confirmation["field"]
            ):
                continue
            latest[(confirmation["member_id"], confirmation["field"])] = confirmation
        return latest

    def list_confirmations(self, tx_id: str, account_id: str) -> dict[str, Any]:
        self._require_account(account_id)
        with self._lock:
            confirmations = [
                item for item in self._confirmations if item["tx_id"] == str(tx_id)
            ]
        items = []
        for confirmation in confirmations:
            if not self._field_allowed(
                account_id, confirmation["table"], confirmation["field"]
            ):
                continue
            effective = self._field_allowed(
                confirmation["confirmed_by"], confirmation["table"], confirmation["field"]
            )
            items.append(self._public_confirmation(confirmation, effective))
        items.sort(key=lambda item: (item["table"], item["field"], item["confirmed_at"]))
        return {"items": items, "total": len(items)}

    # ------------------------------------------------------------------
    # 补证回放与整笔事务证明
    # ------------------------------------------------------------------
    def _attestation_state(self, tx_id: str) -> dict[str, Any]:
        members = self._members_for_tx(tx_id)
        required = [
            (member["member_id"], field)
            for member in members
            for field in member["changed_fields"]
        ]
        confirmed = self._effective_confirmations(tx_id)
        missing = [item for item in required if item not in confirmed]
        member_states = []
        for member in members:
            member_required = [
                field for field in member["changed_fields"]
                if (member["member_id"], field) not in confirmed
            ]
            member_states.append({
                "member_id": member["member_id"],
                "table": member["table"],
                "required_field_count": len(member["changed_fields"]),
                "confirmed_field_count": len(member["changed_fields"]) - len(member_required),
                "missing_field_count": len(member_required),
                "complete": not member_required,
            })
        attested = bool(required) and not missing
        if attested:
            reason = "事务内各成员的实际变更字段均已取得有效复核确认"
        else:
            pending_members = sum(1 for item in member_states if not item["complete"])
            reason = (
                f"整笔事务仍有 {pending_members} 个成员、{len(missing)} 个变更字段"
                "未取得有效复核确认，不能出具已确认证明"
            )
        return {
            "tx_id": str(tx_id),
            "attested": attested,
            "reason": reason,
            "required_field_count": len(required),
            "confirmed_field_count": len(required) - len(missing),
            "missing_field_count": len(missing),
            "pending_member_count": sum(1 for item in member_states if not item["complete"]),
            "members": member_states,
        }

    def replay(self, tx_id: str, account_id: str) -> dict[str, Any]:
        """事件回放：挑出本轮仍需补证的成员，并给出当前账号可见的已确认范围。"""
        self._require_account(account_id)
        members = self._members_for_tx(tx_id)
        confirmations = self._effective_confirmations(tx_id, requester=account_id)
        global_confirmations = self._effective_confirmations(tx_id)
        rendered_members = [
            self._render_member(member, account_id, confirmations, global_confirmations)
            for member in members
        ]
        scope = self._confirmed_scope(tx_id, account_id)
        state = self._attestation_state(tx_id)
        to_supplement = [
            item["member_id"] for item in rendered_members if item["needs_evidence"]
        ]
        pipeline_bus.write_audit(
            "补证回放", str(tx_id),
            (
                f"账号 {account_id} 触发补证回放："
                f"{len(to_supplement)} 个成员仍需补证，"
                f"当前可见已确认字段 {len(scope)} 个"
            ),
            "audit_trail", "已记录",
        )
        return {
            "tx_id": str(tx_id),
            "account_id": account_id,
            "generated_at": _now(),
            "members": rendered_members,
            "members_to_supplement": to_supplement,
            "supplement_member_count": len(to_supplement),
            "confirmed_scope": scope,
            "confirmed_field_count": len(scope),
            "attested": state["attested"],
            "attestation": {
                "attested": state["attested"],
                "reason": state["reason"],
            },
            "notice": "补证成员按事务内全部实际变更字段判定；越权字段只计数量，不显示字段名和值。",
        }

    def attest(self, tx_id: str, account_id: str) -> dict[str, Any]:
        """整笔事务证明：任一成员或字段未获有效复核时拒绝出具。"""
        self._require_account(account_id)
        state = self._attestation_state(tx_id)
        if state["attested"]:
            pipeline_bus.write_audit(
                "事务证明", str(tx_id),
                f"账号 {account_id} 为事务 {tx_id} 出具整笔已确认证明",
                "audit_trail", "已复核",
            )
            return {
                "ok": True,
                "attested": True,
                "message": "整笔事务已获完整复核，可以出具已确认证明",
                "attestation": state,
                "confirmed_scope": self._confirmed_scope(tx_id, account_id),
            }
        pipeline_bus.write_audit(
            "事务证明", str(tx_id),
            (
                f"账号 {account_id} 请求整笔证明被拒绝："
                f"{state['missing_field_count']} 个字段未获有效复核"
            ),
            "audit_trail", "已拒绝",
        )
        return {
            "ok": False,
            "attested": False,
            "message": "整笔事务尚有未获有效复核的成员或字段，不能出具已确认证明",
            "attestation": state,
            "confirmed_scope": self._confirmed_scope(tx_id, account_id),
        }


audit_review_service = AuditReviewService()
