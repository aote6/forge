# Identity Binding / Authentication Boundary Audit

```
Type: Audit / Verification
Authority: Informational
Status: Point-in-time
Scope: Identity attachment vs authentication findings
```

本文是审计记录，**不是** Normative Standard，也**不会**仅因存在本文件而自动约束实现。未来多用户/网络部署结论见文内建议，需单独设计与规范后再实现。


日期: 2026-08-16
状态: KNOWN DESIGN GAP
级别: MINOR（当前本地单用户部署）

## 审计对象

Forge → WRI → veritasd → WorldService → Kernel 的身份来源链。

## 现状

Forge 将 World ObjectId 持久化到 .forge/world_identity，明文存储。
attach_identity(object_id) 发送到 veritasd。
veritasd 是本地 stdin/stdout 进程。
WorldService.attach_identity 只验证 Object 存在且 Alive。
没有验证外部调用主体是否有权声明自己是该 Object。

## 结论

Forge 的 identity persistence 是 ObjectId persistence。
attach_identity 是 identity attachment，而非 authentication。
当前 WRI v1 没有定义外部主体到 World Object 的认证绑定。
veritasd 当前为本地 JSONL stdin/stdout daemon。
因此当前属于本地单用户部署下的 MINOR design gap。

不属于：
- Capability bypass
- Kernel authorization bypass
- Object lifecycle bypass
- Transaction isolation failure

## 未来多用户 / 网络部署必须重新审计

若未来引入多用户、远程客户端或网络 veritasd，
必须在 WRI 层增加 authenticated identity binding，
并重新审计 attach_identity 全链路。

## 当前不修代码的原因

WRI 尚未定义外部主体凭什么证明自己就是某个 Object，
可信凭据是什么形态，认证失败的语义。
在协议定义之前加 token / capability / secret，
会污染已冻结的 Kernel 身份模型。
