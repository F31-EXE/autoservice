import factory

from apps.accounts.models import Organization, Role, ServiceStation, User


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Сеть {n}")


class ServiceStationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ServiceStation

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"СТО {n}")
    city = "Екатеринбург"
    sender_name = "Автосервис"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    organization = factory.SubFactory(OrganizationFactory)
    role = Role.MANAGER
    password = factory.PostGenerationMethodCall("set_password", "pass12345")

    @factory.post_generation
    def stations(self, create, extracted, **kwargs):
        if create and extracted:
            self.stations.set(extracted)
        if create:
            self.save()


class ClientFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "customers.Client"

    station = factory.SubFactory(ServiceStationFactory)
    full_name = factory.Sequence(lambda n: f"Тестов Клиент {n}")
    phone = factory.Sequence(lambda n: f"+7900{n:07d}")


class VehicleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "customers.Vehicle"

    client = factory.SubFactory(ClientFactory)
    make = "Lada"
    model = "Vesta"


class WorkOrderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "workorders.WorkOrder"

    vehicle = factory.SubFactory(VehicleFactory)
    station = factory.LazyAttribute(lambda o: o.vehicle.client.station)
    external_number = factory.Sequence(lambda n: f"WO-{n}")
    opened_at = factory.LazyFunction(lambda: __import__("datetime").date(2024, 1, 15))
    mileage = 50000
    status = "closed"

    @factory.lazy_attribute
    def closed_at(self):
        return self.opened_at if self.status == "closed" else None


class WorkOrderLineFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = "workorders.WorkOrderLine"

    work_order = factory.SubFactory(WorkOrderFactory)
    name = "Замена масла ДВС"
    kind = "labor"
    executed = True
