function resetTriggerLevel(scope)

fprintf(scope,'TRIGger:MAIn:LEVel?');
triggerLevel = str2num(fscanf(scope)); %#ok<*ST2NM> 
triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf(scope,triggerLevel_use);

end