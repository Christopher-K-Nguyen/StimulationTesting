function [filepath_mat,filepath_xlsx] = saveData2(...
    File,...
    filename,...
    filepath)
%% Variables
% Initialize Arrays
Data = File.Data;
numOfChannels = length(Data);
zero_arr = zeros(1,numOfChannels);
channelName_arr = cell(1,numOfChannels);
amplitude_arr = zero_arr;
chargePhase_arr = zero_arr;
chargeInjection_arr = zero_arr;
potentialExcursion_arr = zero_arr;
accessVoltage_arr = zero_arr;
drivingVoltage_arr = zero_arr;

%% File Names
% .mat filename
filename_mat = append(filename,'.mat');       	% filename for .mat file
filepath_mat = fullfile(filepath,filename_mat);  % save path for .mat file

% .xlsx filename
filename_xlsx = append(filename,'.xlsx');           % filename for .xlsx file
filepath_xlsx = fullfile(filepath,filename_xlsx); 	% save path for .xlsx file

%% Save
% Making .mat file
fprintf('Saving MATLAB file...');
save(filepath_mat,'File');             	% save .mat file
fprintf('"%s"\n',filename_mat);         	% .mat file saved

% Making spreadsheet files
fprintf('Saving Excel file...');
for channelNum = 1:numOfChannels
    % loop through channel data
    % Capture data of channel
    time_data = File.Data(channelNum).Time ;              % time data array
    voltage_data = File.Data(channelNum).Voltage;            % voltage data array
    current_data = File.Data(channelNum).Current;          % current data array
    amplitude = File.Data(channelNum).Amplitude;      	% amplitude value
    chargePhase = File.Data(channelNum).ChargePhase;      	% charge per phase value
    surfaceArea = File.Data(channelNum).SurfaceArea;% geometric surface area
    chargeInjection = File.Data(channelNum).ChargeInjection;           % charge injection value
    potentialExcursion_data = File.Data(channelNum).PotentialExcursion;   % maximum cathodal potential value
    accessVoltage_data = File.Data(channelNum).AccessVoltage;
    drivingVoltage_data = File.Data(channelNum).DrivingVoltage;           % driving voltage value
    dateTime = File.Data(channelNum).DateTime;                   % date and time completed
    status = File.Data(channelNum).Status.Description;

    % Initialize Arrays
    data_len = length(time_data);
    cell_arr = cell(data_len,1);	% array of blank space
    amplitude_cell = cell_arr;
    chargePhase_cell = cell_arr;
    surfaceArea_cell = cell_arr;
    chargeInjection_cell = cell_arr;
    potentialExcursion_cell = cell_arr;
    accessVoltage_cell = cell_arr;
    drivingVoltage_cell = cell_arr;
    dateTime_cell = cell_arr;
    status_cell = cell_arr;

    % Potential Excursion Unit
    if any(potentialExcursion_data < 0)
        potentialExcursion_label = 'Emc (V)';
    else
        potentialExcursion_label = 'Ema (V)';
    end

    % Single Values
    amplitude_cell{1} = amplitude;
    chargePhase_cell{1} = chargePhase;
    surfaceArea_cell{1} = surfaceArea;
    chargeInjection_cell{1} = chargeInjection;
    dateTime_cell{1} = dateTime;
    status_cell{1} = status;

    % Multiple Values
    numOfValues = length(potentialExcursion_data);
    for idx = 1:numOfValues
        potentialExcursion_cell{idx} = potentialExcursion_data(idx);
        accessVoltage_cell{idx} = accessVoltage_data(idx);
        drivingVoltage_cell{idx} = drivingVoltage_data(idx);
    end

    % Table
    tableHeading = {...     % heading
        'Time (us)',...     % time
        'Voltage (V)',...   % voltage transient
        'Current (uA)',...  % current stimulation
        'Amplitude (uA)',...
        'Qph (nC/ph)',...   % charge per phase
        'Area (um2)',...     % geometric surface area
        'Qinj (mC/cm2)',... % charge injection
        potentialExcursion_label,...       % max potential
        'Vacc (V)',...
        'Vdrive (V)',...    % driving voltage
        'Date Time',...% date and time
        'Status'};
    tableData = table(...               % table
        time_data,...                   % time
        voltage_data,...                % voltage transient
        current_data,...                % current stimulation
        amplitude_cell,...
        chargePhase_cell,...         % Qph
        surfaceArea_cell,...     % GSA
        chargeInjection_cell,...           % Qinj
        potentialExcursion_cell,...        % Emc
        accessVoltage_cell,...
        drivingVoltage_cell,...        % Vdrive
        dateTime_cell,...            % DateTime
        status_cell,...
        'VariableNames',tableHeading);

    % Save sheet
    channelNum_char = sprintf('Channel %d',channelNum);     % channel number (char)
    channelNum_str = convertCharsToStrings(channelNum_char);% channel number (string)
    writetable(tableData,filepath_xlsx,'Sheet',channelNum_str);    % save sheet to .xlsx file
    channelName_arr{channelNum} = channelNum_char;
    amplitude_arr(channelNum) = amplitude;
    chargePhase_arr(channelNum) = chargePhase;
    chargeInjection_arr(channelNum) = chargeInjection;
    potentialExcursion_arr(channelNum) = potentialExcursion;
    accessVoltage_arr(channelNum) = accessVoltage;
    drivingVoltage_arr(channelNum) = drivingVoltage;
    fprintf('%d...',channelNum);
end

% Compiled Value Sheet
sheetName = 'Values';
data_array = [...
    amplitude_arr;chargePhase_arr;chargeInjection_arr;...
    potentialExcursion_arr;accessVoltage_arr;drivingVoltage_arr];
rowHeadings = {...     % heading
    'Amplitude (uA)',...
    'Qph (nC/ph)',...   % charge per phase
    'Qinj (mC/cm2)',... % charge injection
    potentialExcursion_label,...       % max potential
    'Vacc (V)',...
    'Vdrive (V)'};    % driving voltage
dataTable = array2table(...
    data_array,...
    'VariableNames',channelName_arr,...
    'RowNames',rowHeadings);
writetable(dataTable,filepath_xlsx,'Sheet',sheetName,'WriteRowNames',true);    % save sheet to .xlsx file
fprintf('"%s"\n',filename_xlsx);	% .xlsx file saved

end