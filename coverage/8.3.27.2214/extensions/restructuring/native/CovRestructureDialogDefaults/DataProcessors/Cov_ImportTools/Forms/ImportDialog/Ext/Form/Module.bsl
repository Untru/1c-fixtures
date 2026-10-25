&AtClient
Procedure OnOpen(Cancel)
	SourceName = Cov_DialogSupport.Marker();
EndProcedure

&AtClient
Procedure ConfirmSelection(Command)
	Close(SourceName);
EndProcedure
