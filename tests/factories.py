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
