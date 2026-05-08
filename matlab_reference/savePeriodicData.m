function [] = savePeriodicData(...
    chargePerPhase,...
    subjectName,...
    cycleNum_use,...
    listChannel,...
    channelSelect,...
    numOfChannel,...
    savePath,...
    matVoltageTrans)
%% Constants
N_TO_MICRO = 1e6;
FIRST = 1;
ONE = 1;

%% Function
fprintf('Saving files...\n');

% Starting filename
filename_char = sprintf('%s_Periodic_%02gnC_%s',... % charge per phase
    subjectName,chargePerPhase,cycleNum_use);       % formatted variables

% Making .mat file
filename_mat = append(filename_char,'.mat');       	% filename for .mat file
filename_mat_save = fullfile(savePath,filename_mat);% save path for .mat file
save(filename_mat_save,'matVoltageTrans');          % save .mat file
fprintf('%s\n',filename_mat);                       % .mat file saved

% Making spreadsheet files
filename_xlsx = append(filename_char,'.xlsx');           % filename for .xlsx file
filename_xlsx_save = fullfile(savePath,filename_xlsx); 	% save path for .xlsx file
for channel_idx = 1:numOfChannel    % loop through channel data
    channelNum = channelSelect(channel_idx);
    % Capture data of channel
    time_data = matVoltageTrans(channelNum).Time * N_TO_MICRO;              % time data array
    voltage_data = matVoltageTrans(channelNum).VoltageTransient;            % voltage data array
    current_data = matVoltageTrans(channelNum).CurrentStimulation;          % current data array
    chargePerPhase_data = matVoltageTrans(channelNum).ChargePerPhase;       % charge per phase value
    geomSurfaceArea_data = matVoltageTrans(channelNum).GeometricSurfaceArea;% geometric surface area
    chargeInj_data = matVoltageTrans(channelNum).ChargeInjection;           % charge injection value
    maxPotential_data = matVoltageTrans(channelNum).MaxCathodalPotential;   % maximum cathodal potential value
    voltageDrive_data = matVoltageTrans(channelNum).VoltageDrive;           % driving voltage value
    dateTime_data = matVoltageTrans(channelNum).DateTime;                   % date and time completed
    
    % Arrays for single values
    blank_len = length(time_data);          % length of data
    blank_array = strings(blank_len,ONE);	% array of blank space
    chargePerPhase_str = blank_array;
    chargePerPhase_str(FIRST) = string(chargePerPhase_data);    % add charge per phase to array
    geomSurfaceArea_str = blank_array;
    geomSurfaceArea_str(FIRST) = string(geomSurfaceArea_data);  % add charge per phase to array
    chargeInj_str = blank_array;
    chargeInj_str(FIRST) = string(chargeInj_data);              % add charge injection to array
    maxPotential_str = blank_array;
    maxPotential_str(FIRST) = string(maxPotential_data);        % add max potential to array
    voltageDrive_str = blank_array;
    voltageDrive_str(FIRST) = string(voltageDrive_data);        % add driving voltage to array
    dateTime_str = blank_array;
    dateTime_str(FIRST) = dateTime_data;                        % add date and time to array
    
    % Headings for table
    tableHeading = {...     % heading
        'Time (us)',...     % time
        'Voltage (V)',...   % voltage stimulation
        'Current (uA)',...  % current stimulation
        'Qph (nC/ph)',...   % charge per phase
        'GSA (um2)',...     % geometric surface area
        'Qinj (mC/cm2)',... % charge injection
        'Emc (V)',...       % max cathodal potential
        'Vdrive (V)',...    % driving voltage
        'Completed On'};    % date and time
    
    % Data table
    tableData = table(...               % table
        time_data,...                   % time
        voltage_data,...                % voltage transient
        current_data,...                % current stimulation
        chargePerPhase_str,...          % Qph
        geomSurfaceArea_str,...         % GSA
        chargeInj_str,...               % Qinj
        maxPotential_str,...            % Emc
        voltageDrive_str,...            % Vdrive
        dateTime_str,...                % DateTime
        'VariableNames',tableHeading);  % heading           
    
%     % Make filename for .csv file
	channelName = listChannel(channelNum);
    replaceStim_char = strrep(channelName,'Stimulator ','Stim');  % formatted channel
    replaceCH_char = strrep(replaceStim_char,'Channel ','CH');  % formatted channel
%     csv_char = '.csv';
%     filename_csv_char = append(filename_char,csv_char);
%     pattern = 'C_C';
%     replace = ['C_',channel_char,'_C'];
%     filename_channel_csv_char = strrep(filename_csv_char,pattern,replace);
%     filename_csv = convertCharsToStrings(filename_channel_csv_char);        % filename string for .csv file (string)
%     filename_csv_save = fullfile(savePath,filename_csv);                  % save path for .csv file
    
    % Save spreadsheets
%     writetable(tableData,filename_csv_save);    % save .csv file
%     fprintf('%s\n',filename_csv);   % .csv file saved
    channelName_str = convertCharsToStrings(replaceCH_char);% channel number (string)
    writetable(tableData,filename_xlsx_save,'Sheet',channelName_str);    % save sheet to .xlsx file
%     fprintf('Writing %s to %s file.\n',channelNum_str,filename_xlsx);   % sheet saved to .xlsx file
    
end
fprintf('%s\n',filename_xlsx);	% .xlsx file saved
fprintf('All files saved.\n\n');% all files saved

end