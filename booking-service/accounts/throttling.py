"""Ограничение частоты запросов на входе и регистрации.

Лимит считается по адресу клиента всегда — даже если в запросе есть токен.
Стандартный ScopedRateThrottle для запроса с токеном считает попытки по
пользователю (request.user.pk): тогда любой, у кого есть своя учётная запись,
получал бы отдельный лимит на подбор чужого пароля, а несколько учётных
записей — несколько лимитов.

Адрес клиента: REMOTE_ADDR, если сервис принимает запросы напрямую
(TRUSTED_PROXY_COUNT=0, по умолчанию), или адрес, который дописал в
X-Forwarded-For доверенный прокси (API Gateway, TRUSTED_PROXY_COUNT=1).
Значения X-Forwarded-For, присланные самим клиентом, не учитываются.
"""
from rest_framework.throttling import ScopedRateThrottle


class ClientAddressThrottle(ScopedRateThrottle):
    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
