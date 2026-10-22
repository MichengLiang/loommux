"""Own logical IPython resources independently from MCP transports."""

from loommux.resource.connections import ConnectionRegistry, SessionPresence
from loommux.resource.liveness import LeaseStanding, LeaseVerdict, assess_lease
from loommux.resource.manager import (
    KernelResourceManager,
    ResourceBusyError,
    ResourceManagerError,
    ResourceNotFoundError,
    ResourceProvisionError,
)
from loommux.resource.model import (
    ClientLease,
    KernelRequest,
    KernelResource,
    LeaseClient,
    RequestMode,
    ResourceLifecycle,
)
from loommux.resource.policy import LeaseMode, LeasePolicy, LeasePolicyManager
from loommux.resource.routing import (
    LEASE_POLICY_GENERATION_HEADER,
    OPERATOR_HEADER,
    RESOURCE_HEADER,
    ResourceRoutingError,
    connection_request,
    resolve_client,
    resolve_policy_generation,
)
from loommux.resource.settings import ResourceServerSettings

__all__ = [
    "ClientLease",
    "ConnectionRegistry",
    "KernelResource",
    "KernelResourceManager",
    "KernelRequest",
    "LeaseClient",
    "LeaseMode",
    "LeasePolicy",
    "LeasePolicyManager",
    "LeaseStanding",
    "LeaseVerdict",
    "ResourceServerSettings",
    "ResourceBusyError",
    "ResourceLifecycle",
    "ResourceManagerError",
    "ResourceNotFoundError",
    "ResourceProvisionError",
    "ResourceRoutingError",
    "RequestMode",
    "SessionPresence",
    "RESOURCE_HEADER",
    "OPERATOR_HEADER",
    "LEASE_POLICY_GENERATION_HEADER",
    "assess_lease",
    "connection_request",
    "resolve_client",
    "resolve_policy_generation",
]
