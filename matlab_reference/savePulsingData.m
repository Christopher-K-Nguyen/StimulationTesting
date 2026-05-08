function varargout = savePulsingData( ...
    File, ...
    fileName, ...
    filePath, ...
    varargin)
%% Variables
fprintf('Setting up files...');
if ~isempty(varargin)
    isSetup = true;
    fprintf('\n');
else
    isSetup = false;
end
startTime = tic;
elapseTime = tic;

filePath_mat = '';

% Oscilloscope
% dataLength = File.Oscilloscope(1).Settings.DataLength;
fieldsList = vertcat(File.Oscilloscope(:).Fields);
activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
diffChannel_tf = containsi(fieldsList,{'diff'});
voltageChannel_tf = containsi(fieldsList,{'volt'});
if any(activeChannel_tf)
    specialChannel_idx = find(activeChannel_tf);
elseif any(diffChannel_tf)
    specialChannel_idx = find(diffChannel_tf);
elseif any(voltageChannel_tf)
    specialChannel_idx = find(voltageChannel_tf);
else
    specialChannel_idx = 1;
end
specialChannel = fieldsList(specialChannel_idx);
isVoltageSpecial = contains2(specialChannel,'volt');
hasReturnChannel = contains2(fieldsList,{'ret','count'});

% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelID_cell = {File.Data(:).ID};
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);
if isempty(channelID_cell)
    channelID_cell = cell(numOfGroups,1);
    for groupNum = 1:numOfGroups
        channelID_cell{groupNum} = sprintf('CH%02d',groupNum);
    end
else
    channelID_cell = strrep(channelID_cell,'Channel ','CH');
end
varNameMetric_cell = ['Number of Pulses',channelID_cell];

% Pulsing
% numOfPulses = File.Test.NumberOfPulses;
% periodic = File.Test.Periodic;
% pulseNum_periodic_arr = 0:periodic:numOfPulses;
% numOfPlaces = floor(log10(numOfPulses)) + 1;
% numOfCaptures = length(pulseNum_periodic_arr);
% pulseNum_cell = cell(1,numOfCaptures);
% for idx = 1:numOfCaptures
%     pulseNum_idx = pulseNum_periodic_arr(idx);
%     pulseNum_fix = sprintf("sprintf('Pulse%%0%dd',pulseNum_idx)",numOfPlaces);
%     pulseNum_cell{idx} = eval(pulseNum_fix);
% end
vtNameVT_cell = {'Time (us)' 'CurrentDensity (A-cm2)' '0 pulses'};
updateWaitbar(File);

% Pattern
polarity = File.Parameters.Polarity;
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
switch polarity
    case -1
        potentialExcursion1_label = 'Emc_V';
        potentialExcursion2_label = 'Ema_V';
    case 1
        potentialExcursion1_label = 'Ema_V';
        potentialExcursion2_label = 'Emc_V';
end

% Electrode
activeElectrode = File.Parameters.WorkingElectrode.Type;
refElectrode = File.Parameters.ReferenceElectrode.Type;
returnElectrode = File.Parameters.CounterElectrode.Type;

% Initialize Arrays
data_arr = NaN(1,numOfGroups);

potentialExcursion1_arr = data_arr;
if hasDischargeDelay
    potentialExcursion2_arr = data_arr;
end

drivingVoltage1_arr = data_arr;
drivingVoltage2_arr = data_arr;
accessVoltage1_arr = data_arr;
accessResistance1_arr = data_arr;
if hasInterphaseDelay
    accessVoltage2_arr = data_arr;
    accessVoltage3_arr = data_arr;
    accessResistance2_arr = data_arr;
    accessResistance3_arr = data_arr;
end
if hasDischargeDelay
    accessVoltage4_arr = data_arr;
    accessResistance4_arr = data_arr;
end
if isVoltageSpecial
    activeDriving1_arr = data_arr;
    activeDriving2_arr = data_arr;
end
if hasReturnChannel
    returnDriving1_arr = data_arr;
    returnDriving2_arr = data_arr;
end

%% File Names
if ~isSetup
    % Captures
    pulseNum_arr = vertcat(File.Data(1).Capture(:).PulseNumber);
    captureNum = length(pulseNum_arr);
    rowPosition = sprintf('A%d',captureNum+1);
    pulseNum = pulseNum_arr(captureNum);
    pulseNum_comma = addCommas(pulseNum);
    pulseNum_use = [pulseNum_comma ' pulses'];

    % .mat fileName
    isSaveMAT = true;
    if isSaveMAT
        fileName_mat = [fileName '.mat'];       	% fileName for .mat file
        filePath_mat = fullfile(filePath,fileName_mat);  % save path for .mat file
        tempName_mat = [fileName '_temp.mat'];       	% fileName for .mat file
        tempPath_mat = fullfile(filePath,tempName_mat);  % save path for .mat file
    end
end
updateWaitbar(File);

% .xlsx fileName
isSaveXLSX = true;
hasAutoWidth = false;
if isSaveXLSX
    fileName_xlsx = [fileName '.xlsx'];           % fileName for .xlsx file
    filePath_xlsx = fullfile(filePath,fileName_xlsx); 	% save path for .xlsx file
    % tempName_xlsx = [fileName '_temp.xlsx'];       	% fileName for .mat file
    % tempPath_xlsx = fullfile(filePath,tempName_xlsx);  % save path for .mat file
    while isSetup
        % try
        % if isfile(filePath_xlsx)
        %     delete(filePath_xlsx);
        % end
        % Electrode Polarization
        fprintf('\t%s...',potentialExcursion1_label);
        updateWaitbar(File,['Writing ' potentialExcursion1_label '...']);
        checkTime = tic;
        writecell(varNameMetric_cell,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',potentialExcursion1_label);
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);

        if hasDischargeDelay
            fprintf('\t%s...',potentialExcursion2_label);
            updateWaitbar(File,['Writing ' potentialExcursion2_label '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',potentialExcursion2_label);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end
        
        % Driving Voltage
        sheetName = 'Vd1_V';
        fprintf('\t%s...',sheetName);
        updateWaitbar(File,['Writing ' sheetName '...']);
        checkTime = tic;
        writecell(varNameMetric_cell,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName);
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);

        sheetName = 'Vd2_V';
        fprintf('\t%s...',sheetName);
        updateWaitbar(File,['Writing ' sheetName '...']);
        checkTime = tic;
        writecell(varNameMetric_cell,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName);
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);

        % Access Voltage
        sheetName = 'Val1_V';
        fprintf('\t%s...',sheetName);
        updateWaitbar(File,['Writing ' sheetName '...']);
        checkTime = tic;
        writecell(varNameMetric_cell,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName);
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);

        if hasInterphaseDelay
            sheetName = 'Vat1_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);

            sheetName = 'Val2_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end

        if hasDischargeDelay
            sheetName = 'Vat2_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end

        
        
        % Access Resistance
        sheetName = 'Ral1_kOhm';
        fprintf('\t%s...',sheetName);
        updateWaitbar(File,['Writing ' sheetName '...']);
        checkTime = tic;
        writecell(varNameMetric_cell,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName);
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
        updateWaitbar(File,['Writing ' sheetName '...']);

        if hasInterphaseDelay
            sheetName = 'Rat1_kOhm';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
            
            sheetName = 'Ral2_kOhm';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end

        if hasDischargeDelay
            sheetName = 'Rat2_kOhm';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end

        % Driving Potential
        if ~isVoltageSpecial
            sheetName = 'Eda1_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);

            sheetName = 'Eda2_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end
        if hasReturnChannel
            sheetName = 'Edr1_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);

            sheetName = 'Edr2_V';
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(varNameMetric_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        end

        % Voltage Transient
        % fprintf('Transients...\n');
        for groupNum = 1:numOfGroups
            % Active
            channelID = channelID_cell{groupNum};
            if ~isVoltageSpecial
                sheetName = ['PT_V_' channelID];
            else
                sheetName = ['VT_V_' channelID];
            end
            fprintf('\t%s...',sheetName);
            updateWaitbar(File,['Writing ' sheetName '...']);
            checkTime = tic;
            writecell(vtNameVT_cell,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName);
            [endTime,unit] = getEndTime(checkTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);

            if hasReturnChannel
                % Return
                sheetName = ['RPT_V_' channelID];
                fprintf('\t%s...',sheetName);
                updateWaitbar(File,['Writing ' sheetName '...']);
                checkTime = tic;
                writecell(vtNameVT_cell,filePath_xlsx, ...
                    'FileType','spreadsheet', ...
                    'UseExcel',true, ...
                    'AutoFitWidth',hasAutoWidth, ...
                    'Sheet',sheetName);
                [endTime,unit] = getEndTime(checkTime);
                fprintf('OK (%.2f %s)\n',endTime,unit);
            else
                fprintf('\n');
            end
        end
        break;
        % catch
        % end
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('"%s" (%.2f %s)\n',fileName_xlsx,endTime,unit);
updateWaitbar(File);

if ~isSetup
    %% Close Files
    fclose('all');
    fprintf('\t');
    File = setScopeStatus(File,'close');
    File = rmfield(File,'Oscilloscope');

    %% MATLAB File
    % Making .mat file
    if isSaveMAT
        fprintf('Saving MATLAB file...');
        updateWaitbar(File,'Saving MATLAB file...');
        startTime = tic;
        if groupNum == numOfGroups
            File.Oscilloscope.Object = [];
        end
        varName = getVarName(File);
        newVarName = ['File_' fileName];
        setNewVarName = sprintf('%s = %s;',newVarName,varName);
        eval(setNewVarName);
        while true
            try
                try
                    save(filePath_mat,newVarName);             	% save .mat file
                catch
                    save(tempPath_mat,newVarName);             	% save .mat file
                    delete(filePath_mat);
                    movefile(tempPath_mat,filePath_mat,'f');
                end
                break;
            catch
            end
        end
        varargout{1} = filePath_mat;
        [endTime,unit] = getEndTime(startTime);
        fprintf('"%s" (%.2f %s)\n',fileName_mat,endTime,unit);         	% .mat file saved
    end

    %% Excel File
    fprintf('Saving Excel file...\n');
    fprintf('\tExtracting data...');
    startTime = tic;
    for groupNum = 1:numOfGroups % loop through channel data
        % Extract
        potentialExcursion_data = File.Data(groupNum).Capture(captureNum).PotentialExcursion;
        drivingVoltage_data = File.Data(groupNum).Capture(captureNum).DrivingVoltage;           % driving voltage value
        accessVoltage_data = File.Data(groupNum).Capture(captureNum).AccessVoltage;
        accessResistance_data = File.Data(groupNum).Capture(captureNum).AccessResistance;
        drivingPotential_data = File.Data(groupNum).Capture(captureNum).DrivingPotential;

        % Potential Excursion
        potentialExcursion1_arr(groupNum) = potentialExcursion_data(1);
        if hasDischargeDelay
            potentialExcursion2_arr(groupNum) = potentialExcursion_data(2);
        end

        % Driving Voltage
        drivingVoltage1_arr(groupNum) = drivingVoltage_data(1);
        drivingVoltage2_arr(groupNum) = drivingVoltage_data(2);

        % Access
        accessVoltage1_arr(groupNum) = accessVoltage_data(1);
        accessVoltage2_arr(groupNum) = accessVoltage_data(2);
        accessResistance1_arr(groupNum)= accessResistance_data(1);
        accessResistance2_arr(groupNum)= accessResistance_data(2);
        if hasInterphaseDelay
            accessVoltage3_arr(groupNum) = accessVoltage_data(3); %#ok<*UNRCH>
                accessVoltage4_arr(groupNum) = accessVoltage_data(4);
                accessResistance3_arr(groupNum)= accessResistance_data(3);
                accessResistance4_arr(groupNum)= accessResistance_data(4);
        end

        % Driving Potential
        if ~isVoltageSpecial
            activeDriving1_arr(groupNum) = drivingPotential_data(1);
            activeDriving2_arr(groupNum) = drivingPotential_data(2);
        end
        if hasReturnChannel
            returnDriving1_arr(groupNum)= drivingPotential_data(3);
            returnDriving2_arr(groupNum)= drivingPotential_data(4);
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);	% .xlsx file saved

    %% Writing Metrics
    fprintf('\tWriting metrics...');
    startTime = tic;
    % Electrode Polarization
    potentialExcursion1_mat = [pulseNum potentialExcursion1_arr];
    fprintf([potentialExcursion1_label '...']);
    updateWaitbar(File,['Writing ' potentialExcursion1_label '...']);
    writematrix(potentialExcursion1_mat,filePath_xlsx, ...
        'FileType','spreadsheet', ...
        'UseExcel',true, ...
        'AutoFitWidth',hasAutoWidth, ...
        'Sheet',potentialExcursion1_label, ...
        'Range',rowPosition);
    
    if hasDischargeDelay
        potentialExcursion2_mat = [pulseNum potentialExcursion2_arr];
        fprintf([potentialExcursion2_label '...']);
        updateWaitbar(File,['Writing ' potentialExcursion2_label '...']);
        writematrix(potentialExcursion2_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',potentialExcursion2_label, ...
            'Range',rowPosition);
    end

    % Driving Voltage
    drivingVoltage1_mat = [pulseNum drivingVoltage1_arr];
    sheetName = 'Vd1_V';
    fprintf([sheetName '...']);
    updateWaitbar(File,['Writing ' sheetName '...']);
    writematrix(drivingVoltage1_mat,filePath_xlsx, ...
        'FileType','spreadsheet', ...
        'UseExcel',true, ...
        'AutoFitWidth',hasAutoWidth, ...
        'Sheet',sheetName, ...
        'Range',rowPosition);
    
    drivingVoltage2_mat = [pulseNum drivingVoltage2_arr];
    sheetName = 'Vd2_V';
    fprintf([sheetName '...']);
    updateWaitbar(File,['Writing ' sheetName '...']);
    writematrix(drivingVoltage2_mat,filePath_xlsx, ...
        'FileType','spreadsheet', ...
        'UseExcel',true, ...
        'AutoFitWidth',hasAutoWidth, ...
        'Sheet',sheetName, ...
        'Range',rowPosition);

    % Access Voltage
    accesssVoltage1_mat = [pulseNum accessVoltage1_arr];
    sheetName = 'Val1_V';
    fprintf([sheetName '...']);
    updateWaitbar(File,['Writing ' sheetName '...']);
    writematrix(accesssVoltage1_mat,filePath_xlsx, ...
        'FileType','spreadsheet', ...
        'UseExcel',true, ...
        'AutoFitWidth',hasAutoWidth, ...
        'Sheet',sheetName, ...
        'Range',rowPosition);

    if hasInterphaseDelay
        accesssVoltage3_mat = [pulseNum accessVoltage3_arr];
        sheetName = 'Vat1_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssVoltage3_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);

        accesssVoltage2_mat = [pulseNum accessVoltage2_arr];
        sheetName = 'Val2_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssVoltage2_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end

    if hasDischargeDelay
        accesssVoltage4_mat = [pulseNum accessVoltage4_arr];
        sheetName = 'Vat2_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssVoltage4_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end

    % Access Resistance
    accesssRessistance1_mat = [pulseNum accessResistance1_arr];
    sheetName = 'Ral1_kOhm';
    fprintf([sheetName '...']);
    updateWaitbar(File,['Writing ' sheetName '...']);
    writematrix(accesssRessistance1_mat,filePath_xlsx, ...
        'FileType','spreadsheet', ...
        'UseExcel',true, ...
        'AutoFitWidth',hasAutoWidth, ...
        'Sheet',sheetName, ...
        'Range',rowPosition);    

    if hasInterphaseDelay
        accesssRessistance2_mat = [pulseNum accessResistance2_arr];
        sheetName = 'Ral2_kOhm';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssRessistance2_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);

        accesssRessistance3_mat = [pulseNum accessResistance3_arr];
        sheetName = 'Rat1_kOhm';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssRessistance3_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end

    if hasDischargeDelay
        accesssRessistance4_mat = [pulseNum accessResistance4_arr];
        sheetName = 'Rat2_kOhm';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(accesssRessistance4_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end

    % Driving Potential
    if ~isVoltageSpecial
        activeDriving1_mat = [pulseNum activeDriving1_arr];
        sheetName = 'Eda1_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(activeDriving1_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);

        activeDriving2_mat = [pulseNum activeDriving2_arr];
        sheetName = 'Eda2_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(activeDriving2_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end
    if hasReturnChannel
        returnDriving1_mat = [pulseNum returnDriving1_arr];
        sheetName = 'Edr1_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(returnDriving1_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);

        returnDriving2_mat = [pulseNum returnDriving2_arr];
        sheetName = 'Edr2_V';
        fprintf([sheetName '...']);
        updateWaitbar(File,['Writing ' sheetName '...']);
        writematrix(returnDriving2_mat,filePath_xlsx, ...
            'FileType','spreadsheet', ...
            'UseExcel',true, ...
            'AutoFitWidth',hasAutoWidth, ...
            'Sheet',sheetName, ...
            'Range',rowPosition);
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);	% .xlsx file saved

    %% Writing Waveforms
    fprintf('\tWriting waveforms...');
    startTime = tic;
    if captureNum == 1
        time = File.Data(groupNum).Capture(captureNum).Time;
        currentDensity = File.Data(groupNum).Capture(captureNum).CurrentDensity;
    else
        colNum = captureNum + 2;
        colLetter = num2ExcelCol(colNum);
        colPosition = [colLetter '1'];
    end
    for groupNum = 1:numOfGroups
        channelID = channelID_cell{groupNum};
        fprintf([channelID '...']);
        if ~isVoltageSpecial
            sheetName = ['PT_V_' channelID];
            arr = File.Data(groupNum).Capture(captureNum).Active;
        else
            sheetName = ['VT_V_' channelID];
            arr = File.Data(groupNum).Capture(captureNum).Voltage;
        end
        updateWaitbar(File,['Writing ' sheetName '...']);
        if captureNum > 1
            arr_cell = num2cell(arr);
            cell_use = [pulseNum_use;arr_cell];
            writecell(cell_use,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName, ...
                'Range',colPosition);
        else
            dataMat = [time currentDensity arr];
            writematrix(dataMat,filePath_xlsx, ...
                'FileType','spreadsheet', ...
                'UseExcel',true, ...
                'AutoFitWidth',hasAutoWidth, ...
                'Sheet',sheetName, ...
                'Range','A2');
        end
        
        if hasReturnChannel
            sheetName = ['RPT_V_' channelID];
            updateWaitbar(File,['Writing ' sheetName '...']);
            arr = File.Data(groupNum).Capture(captureNum).Return;
            if captureNum > 1
                arr_cell = num2cell(arr);
                cell_use = [pulseNum_use;arr_cell];
                writecell(cell_use,filePath_xlsx, ...
                    'FileType','spreadsheet', ...
                    'UseExcel',true, ...
                    'AutoFitWidth',hasAutoWidth, ...
                    'Sheet',sheetName, ...
                    'Range',colPosition);
            else
                dataMat = [time currentDensity arr];
                writematrix(dataMat,filePath_xlsx, ...
                    'FileType','spreadsheet', ...
                    'UseExcel',true, ...
                    'AutoFitWidth',hasAutoWidth, ...
                    'Sheet',sheetName, ...
                    'Range','A2');
            end
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);	% .xlsx file save
    [endTime,unit] = getEndTime(elapseTime);
    fprintf('Time elapsed: %.2f %s\n',endTime,unit);
end
updateWaitbar(File);
varargout{1} = filePath_mat;
varargout{2} = filePath_xlsx;

end