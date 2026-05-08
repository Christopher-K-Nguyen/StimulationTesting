function [File,isQuit] = setOscilloscopeTrigger(File,varargin)
%% Variables
isQuit = false;
if ~isempty(varargin)
    var = varargin{1};
    isEdge = contains2(var,'edg');
    isVideo = contains2(var,'vid');
    if isEdge
        trigType = 'EDGe';
        trigMode = 'NORMal';
    elseif isVideo
        trigType = 'VIDeo';
        trigMode = 'AUTO';
    end
else
    trigType = 'EDGe';
    trigMode = 'NORMal';
    isEdge = true;
end
numOfDevices = length(File.Oscilloscope);

%% Trigger
for deviceNum = 1:numOfDevices
    make = File.Oscilloscope(deviceNum).Make;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    fprintf('Setting %s %s trigger...',make,model);
    startTime = tic;

    % Type
    trigType_use = sprintf('TRIGger:MAIn:TYPe %s',trigType);
    if isTPS
        trigType_check = '';
        while ~contains2(trigType_check,trigType)
            fprintf(oscilloscope,trigType_use);          % trigger type
            fprintf(oscilloscope,'TRIGger:MAIn:TYPe?');
            trigType_check = fgetl2(oscilloscope);
        end
    else
        fprintf(oscilloscope,trigType_use);          % trigger type
    end
    fprintf('%s...',trigType);

    % Source
    trigSource = 'EXT';
    trigSource_use = sprintf('TRIGger:MAIn:EDGe:SOUrce %s',trigSource);
    if isTPS
        trigSource_check = '';
    while ~contains2(trigSource_check,trigSource)
        fprintf(oscilloscope,trigSource_use);          % trigger source
        fprintf(oscilloscope,'TRIGger:MAIn:EDGe:SOUrce?');
        trigSource_check = fgetl2(oscilloscope);
    end
    else
        fprintf(oscilloscope,trigSource_use);          % trigger source
    end
    File.Oscilloscope(deviceNum).Trigger = trigSource;
    fprintf('%s...',trigSource);

    % Slope
    if isEdge
        trigSlope = 'RISe';
        slope_use = sprintf('TRIGger:MAIn:EDGe:SLOpe %s',trigSlope);
        if isTPS
            trigSlope_check = '';
            while ~contains2(trigSlope_check,trigSlope)
                fprintf(oscilloscope,slope_use);  % trigger on rising slope
                fprintf(oscilloscope,'TRIGger:MAIn:EDGe:SLOpe?');
                trigSlope_check = fgetl2(oscilloscope);
            end
        else
            fprintf(oscilloscope,slope_use);  % trigger on rising slope
        end
        fprintf('%s...',trigSlope);
    end

    % Normal Mode
    trigMod_use = sprintf('TRIGger:MAIn:MODe %s',trigMode);
    % fprintf(oscilloscope,'*CLS');
    if isTPS
        trigMode_check = '';
        while ~contains2(trigMode_check,trigMode)
            fprintf(oscilloscope,trigMod_use);
            fprintf(oscilloscope,'TRIGger:MAIn:MODe?');
            trigMode_check = fgetl2(oscilloscope);
        end
    else
        fprintf(oscilloscope,trigMod_use);
    end
    fprintf('%s...',trigMode);

    % Coupling
    trigCoupling = 'DC';
    trigCoupling_use = sprintf('TRIGger:MAIn:EDGE:COUPling %s',trigCoupling);
    if isTPS
    couplingTime = tic;
    trigCoupling_check = '';
    while ~contains2(trigCoupling_check,trigCoupling)
        fprintf(oscilloscope,trigCoupling_use);         % trigger on DC coupling
        fprintf(oscilloscope,'TRIGger:MAIn:EDGE:COUPling?');
        trigCoupling_check = fgetl2(oscilloscope);
        if toc(couplingTime) > 1
            break;
        end
    end
    else
        fprintf(oscilloscope,trigCoupling_use);         % trigger on DC coupling
    end
    fprintf('%s...',trigCoupling);

    % Adjust trigger level
    if isEdge
        triggerLevel = 0.2;
        triggerLevel_use = sprintf('TRIGger:MAIn:LEVel %.2e',triggerLevel);
        if isTPS
            triggerTime = tic;
            triggerLevel_check = 0;
            %     triggerLevel_use = 'TRIGger:MAIn SETLevel';
            while ~isequal(triggerLevel_check,triggerLevel)
                fprintf(oscilloscope,triggerLevel_use);
                fprintf(oscilloscope,'TRIGger:MAIn:LEVel?');
                triggerLevel_raw = fgetl2(oscilloscope);
                triggerLevel_check = str2double(triggerLevel_raw);
                if toc(triggerTime) > 1
                    break;
                end
            end
        else
            fprintf(oscilloscope,triggerLevel_use);
        end
        fprintf('%.2e V',triggerLevel);
        [endTime,unit] = getEndTime(startTime);
        fprintf(' (%.2f %s)\n',endTime,unit);
    else
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
    end
end

end